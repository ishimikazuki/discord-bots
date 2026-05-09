"""Scrape Epos Net 月別ご利用履歴照会 for transaction data.

Login flow:
  1. POST id/password to login_preload.do
  2. (occasionally) ご本人様確認: enter 3-digit CVV via on-screen keypad
  3. GET use_history_preload.do, optionally select year/month, parse table

Credentials come from macOS keychain (account=epos-email|epos-pass|epos-cvv,
service=epos-net). Cookie state is persisted to data/epos_storage_state.json
so subsequent runs can skip the CVV prompt when Epos Net trusts the device.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from card_summary.store import Transaction

log = logging.getLogger(__name__)

EPOS_LOGIN_URL = "https://www.eposcard.co.jp/memberservice/pc/login/login_preload.do"
EPOS_HISTORY_URL = (
    "https://www.eposcard.co.jp/memberservice/pc/usehistoryreference/use_history_preload.do"
)
STORAGE_STATE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "epos_storage_state.json"
)


class EposCredentialsError(RuntimeError):
    """Raised when a required keychain credential cannot be read."""


class EposLoginChallengeError(RuntimeError):
    """Raised when Epos Net requires a human-only verification challenge."""


def get_credential(account: str, service: str = "epos-net") -> str:
    """Return a credential value from macOS keychain. Raises on missing entry."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise EposCredentialsError(
            f"keychain lookup failed for account={account} service={service}: {e.stderr.strip()}"
        )
    except FileNotFoundError:
        raise EposCredentialsError("'security' command not found (macOS only)")
    return result.stdout.strip()


def make_source_id(occurred_at: str, merchant: str, amount: int, idx: int = 0) -> str:
    """Stable source_id for an Epos Net row.

    Epos rows have no native unique id, so we hash (date|merchant|amount|row_index).
    `idx` disambiguates repeat purchases at the same merchant on the same day for
    the same amount within a single scrape.
    """
    h = hashlib.sha256(f"{occurred_at}|{merchant}|{amount}|{idx}".encode("utf-8")).hexdigest()
    return f"epos:{h[:16]}"


@dataclass(frozen=True)
class ScrapedRow:
    occurred_at: str
    merchant: str
    amount: int


def _parse_amount(raw: str) -> int | None:
    """'1,518円' -> 1518, '-3,200円' -> -3200, returns None if not parseable."""
    cleaned = raw.replace(",", "").replace("円", "").replace(" ", "").strip()
    if not cleaned:
        return None
    if not re.fullmatch(r"-?\d+", cleaned):
        return None
    return int(cleaned)


def _parse_date(raw: str) -> str | None:
    """'2026/5/1' -> '2026-05-01T00:00:00'. None on failure."""
    parts = raw.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        y, m, d = (int(p) for p in parts)
        return datetime(y, m, d).isoformat()
    except ValueError:
        return None


def rows_to_transactions(rows: list[list[str]]) -> list[Transaction]:
    """Convert scraped table rows (list of cells) into Transaction objects.

    Expected cell order from `月別ご利用履歴照会`:
      [date, merchant, content, amount(yen), payment_div, start_month, note]

    Rows that do not parse as a valid (date, amount) pair are skipped.
    """
    out: list[Transaction] = []
    occurrence_counts: dict[tuple[str, str, int], int] = {}
    for cells in rows:
        if len(cells) < 4:
            continue
        occurred_at = _parse_date(cells[0])
        if occurred_at is None:
            continue
        merchant = cells[1].strip()
        amount = _parse_amount(cells[3])
        if amount is None or not merchant:
            continue
        occurrence_key = (occurred_at, merchant, amount)
        occurrence_idx = occurrence_counts.get(occurrence_key, 0)
        occurrence_counts[occurrence_key] = occurrence_idx + 1
        out.append(Transaction(
            occurred_at=occurred_at,
            merchant=merchant,
            amount=amount,
            category=None,
            source="epos_net",
            source_id=make_source_id(occurred_at, merchant, amount, occurrence_idx),
        ))
    return out


def _apple_script_literal(value: str) -> str:
    """Return an AppleScript string literal for `value`.

    JSON string syntax is accepted by AppleScript for the escapes we need, and
    keeps credentials out of process arguments because we pass the full script
    through stdin to `osascript`.
    """
    return json.dumps(value, ensure_ascii=False)


def _run_osascript(script: str, *, timeout: int = 120) -> str:
    """Run AppleScript via stdin and return stdout."""
    try:
        result = subprocess.run(
            ["osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except FileNotFoundError as e:
        raise EposLoginChallengeError("osascript is not available on this host") from e
    except subprocess.TimeoutExpired as e:
        raise EposLoginChallengeError("Chrome AppleScript Epos scrape timed out") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        if "JavaScript" in stderr and "Apple" in stderr:
            raise EposLoginChallengeError(
                "Chrome blocks AppleScript JavaScript; enable View > Developer > Allow JavaScript from Apple Events"
            ) from e
        raise EposLoginChallengeError(
            f"Chrome AppleScript Epos scrape failed: {stderr or 'unknown osascript error'}"
        ) from e
    return result.stdout.strip()


def _parse_chrome_history_payload(payload: str) -> list[Transaction]:
    """Convert the JSON payload returned by Chrome AppleScript into transactions."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise EposLoginChallengeError("Chrome AppleScript returned non-JSON Epos data") from e
    if not data.get("ok"):
        reason = data.get("reason") or "unknown"
        raise EposLoginChallengeError(f"Chrome AppleScript Epos scrape failed: {reason}")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise EposLoginChallengeError("Chrome AppleScript Epos scrape returned no rows array")
    return rows_to_transactions(rows)


def _build_chrome_history_script(*, year: int, month: int, email: str, password: str, cvv: str) -> str:
    """Build a self-contained AppleScript that logs in through trusted Chrome."""
    login_js = f"""
(() => {{
  const email = {json.dumps(email)};
  const password = {json.dumps(password)};
  const body = document.body ? document.body.innerText : '';
  if (body.includes('画像認証') || body.includes('パズル')) {{
    return JSON.stringify({{ok:false, reason:'image verification required'}});
  }}
  const login = document.querySelector("input[name='loginId'], input[id*='loginId'], input[id='userid']");
  const pw = document.querySelector("input[name='passWord'], input[id='passWord'], input[name='loginPassword'], input[type='password']");
  if (!login || !pw) {{
    return JSON.stringify({{ok:true, action:'login-not-needed', title:document.title, url:location.href}});
  }}
  const setVal = (el, val) => {{
    el.focus();
    el.value = val;
    el.dispatchEvent(new Event('input', {{bubbles:true}}));
    el.dispatchEvent(new Event('change', {{bubbles:true}}));
  }};
  setVal(login, email);
  setVal(pw, password);
  const submit = Array.from(document.querySelectorAll("button,input[type='submit'],input[type='button'],input[type='image']"))
    .find(el => (el.value || el.textContent || el.getAttribute('alt') || '').includes('ログイン'));
  if (!submit) return JSON.stringify({{ok:false, reason:'login submit missing'}});
  submit.click();
  return JSON.stringify({{ok:true, action:'submitted-login'}});
}})()
"""
    cvv_js = f"""
(() => {{
  const cvv = {json.dumps(cvv)};
  const body = document.body ? document.body.innerText : '';
  if (body.includes('画像認証') || body.includes('パズル')) {{
    return JSON.stringify({{ok:false, reason:'image verification required'}});
  }}
  if (!body.includes('ご本人様確認')) {{
    return JSON.stringify({{ok:true, action:'cvv-not-needed', title:document.title, url:location.href}});
  }}
  const findButton = (label) => Array.from(document.querySelectorAll("button,input[type='button'],input[type='submit']"))
    .find(el => (el.value || el.textContent || '').trim() === label);
  for (const digit of cvv) {{
    const button = findButton(digit);
    if (!button) return JSON.stringify({{ok:false, reason:`cvv digit button missing: ${{digit}}`}});
    button.click();
  }}
  const next = Array.from(document.querySelectorAll("button,input[type='button'],input[type='submit']"))
    .find(el => (el.value || el.textContent || '').includes('次へ'));
  if (!next) return JSON.stringify({{ok:false, reason:'cvv next button missing'}});
  next.click();
  return JSON.stringify({{ok:true, action:'submitted-cvv'}});
}})()
"""
    month_js = f"""
(() => {{
  const body = document.body ? document.body.innerText : '';
  if (body.includes('通信エラーが発生しました') || body.includes('もう一度ログイン')) {{
    return JSON.stringify({{ok:false, reason:'login session rejected by Epos Net'}});
  }}
  const form = document.forms['useHistoryPForm'] || document.querySelector("form[action*='use_history_dispatch']");
  if (!form) return JSON.stringify({{ok:false, reason:'history form missing', title:document.title, url:location.href, text:body.slice(0, 160)}});
  const yearSelect = form.querySelector("select[name='monthSelectTagsDateYear']");
  const monthSelect = form.querySelector("select[name='monthSelectTagsDateMonth']");
  if (!yearSelect || !monthSelect) return JSON.stringify({{ok:false, reason:'history selects missing'}});
  yearSelect.value = {json.dumps(str(year))};
  monthSelect.value = {json.dumps(f"{month:02d}")};
  yearSelect.dispatchEvent(new Event('change', {{bubbles:true}}));
  monthSelect.dispatchEvent(new Event('change', {{bubbles:true}}));
  form.submit();
  return JSON.stringify({{ok:true, action:'submitted-history-month', year:yearSelect.value, month:monthSelect.value}});
}})()
"""
    extract_js = """
(() => {
  const body = document.body ? document.body.innerText : '';
  if (body.includes('通信エラーが発生しました') || body.includes('もう一度ログイン')) {
    return JSON.stringify({ok:false, reason:'login session rejected by Epos Net'});
  }
  const rows = [];
  for (const table of Array.from(document.querySelectorAll('table'))) {
    if (!table.innerText.includes('ご利用場所') || !table.innerText.includes('ご利用金額')) continue;
    for (const tr of Array.from(table.querySelectorAll('tr'))) {
      const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
      if (cells.length >= 4 && /^\\d{4}\\/\\d{1,2}\\/\\d{1,2}$/.test(cells[0])) rows.push(cells);
    }
  }
  if (!rows.length && !body.includes('ご利用履歴はございません')) {
    return JSON.stringify({ok:false, reason:'history rows missing', title:document.title, url:location.href, text:body.slice(0, 160)});
  }
  return JSON.stringify({ok:true, rows});
})()
"""
    return f"""
tell application "Google Chrome"
  if not (exists window 1) then make new window
  set scrapeTab to make new tab at end of tabs of window 1 with properties {{URL:"{EPOS_LOGIN_URL}"}}
  set active tab index of window 1 to (count of tabs of window 1)
  try
    delay 2
    repeat 40 times
      if loading of scrapeTab is false then exit repeat
      delay 0.5
    end repeat
    set loginResult to execute scrapeTab javascript {_apple_script_literal(login_js)}
    if loginResult contains "\\"ok\\":false" then
      close scrapeTab
      return loginResult
    end if
    delay 4
    repeat 40 times
      if loading of scrapeTab is false then exit repeat
      delay 0.5
    end repeat
    set cvvResult to execute scrapeTab javascript {_apple_script_literal(cvv_js)}
    if cvvResult contains "\\"ok\\":false" then
      close scrapeTab
      return cvvResult
    end if
    delay 3
    repeat 40 times
      if loading of scrapeTab is false then exit repeat
      delay 0.5
    end repeat
    set URL of scrapeTab to "{EPOS_HISTORY_URL}"
    delay 2
    repeat 40 times
      if loading of scrapeTab is false then exit repeat
      delay 0.5
    end repeat
    set monthResult to execute scrapeTab javascript {_apple_script_literal(month_js)}
    if monthResult contains "\\"ok\\":false" then
      close scrapeTab
      return monthResult
    end if
    delay 3
    repeat 40 times
      if loading of scrapeTab is false then exit repeat
      delay 0.5
    end repeat
    set finalResult to execute scrapeTab javascript {_apple_script_literal(extract_js)}
    close scrapeTab
    return finalResult
  on error errMsg number errNum
    try
      close scrapeTab
    end try
    error errMsg number errNum
  end try
end tell
"""


def _fetch_month_history_with_chrome_apple_events(year: int, month: int) -> list[Transaction]:
    """Fetch Epos history through the user's trusted Google Chrome profile."""
    email = get_credential("epos-email")
    password = get_credential("epos-pass")
    cvv = get_credential("epos-cvv")
    script = _build_chrome_history_script(year=year, month=month, email=email, password=password, cvv=cvv)
    payload = _run_osascript(script)
    return _parse_chrome_history_payload(payload)


async def fetch_month_history(year: int, month: int, *, headless: bool = True) -> list[Transaction]:
    """Login to Epos Net, navigate to 月別ご利用履歴照会, and return scraped Transactions.

    Selectors below were captured from the live page on 2026-05-09 and may need
    adjustment if Epos Net changes its DOM. Run with `headless=False` for visual
    debugging.
    """
    from playwright.async_api import async_playwright  # lazy: optional dep

    email = get_credential("epos-email")
    password = get_credential("epos-pass")
    cvv = get_credential("epos-cvv")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            try:
                ctx_kwargs: dict = {}
                if STORAGE_STATE_PATH.exists():
                    ctx_kwargs["storage_state"] = str(STORAGE_STATE_PATH)
                context = await browser.new_context(**ctx_kwargs)
                page = await context.new_page()

                await page.goto(EPOS_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

                # Step 1: ID / password (skipped when storage_state already authenticated)
                login_id = page.locator("input[name='loginId'], input[id*='loginId'], input[id='userid']")
                if await login_id.count() > 0 and await login_id.first.is_visible():
                    await login_id.first.fill(email)
                    await page.locator(
                        "input[name='passWord'], input[id='passWord'], input[name='loginPassword'], input[type='password']"
                    ).first.fill(password)
                    await page.locator("button:has-text('ログイン'), input[value='ログイン']").first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=60000)

                body_text = await page.locator("body").inner_text(timeout=10000)
                if "画像認証" in body_text or "パズルを完成" in body_text:
                    raise EposLoginChallengeError(
                        "Epos Net requested image/puzzle verification; complete login in a trusted Chrome profile first"
                    )

                # Step 2 (optional): ご本人様確認 — 3-digit CVV via on-screen keypad
                if await page.locator("text=ご本人様確認").count() > 0:
                    log.info("CVV prompt detected; entering security code via keypad")
                    for digit in cvv:
                        await page.locator(f"button:has-text('{digit}')").first.click()
                    await page.locator(
                        "button:has-text('次へ'), input[value='次へ']"
                    ).first.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=60000)

                # Step 3: 月別ご利用履歴照会
                await page.goto(EPOS_HISTORY_URL, wait_until="domcontentloaded", timeout=60000)
                history_text = await page.locator("body").inner_text(timeout=10000)
                if "通信エラーが発生しました" in history_text or "もう一度ログイン" in history_text:
                    raise EposLoginChallengeError(
                        "Epos Net did not preserve the login session; refresh storage_state in a trusted browser"
                    )
                try:
                    await page.locator("select").first.wait_for(timeout=5000)
                    year_select = page.locator("select[name='monthSelectTagsDateYear']")
                    month_select = page.locator("select[name='monthSelectTagsDateMonth']")
                    if await year_select.count() == 0:
                        year_select = page.locator("select").nth(0)
                    if await month_select.count() == 0:
                        month_select = page.locator("select").nth(1)
                    await year_select.select_option(str(year))
                    await month_select.select_option(f"{month:02d}")
                    await page.evaluate(
                        """() => {
                            const form = document.forms['useHistoryPForm']
                                || document.querySelector("form[action*='use_history_dispatch']");
                            if (form) form.submit();
                        }"""
                    )
                    await page.wait_for_load_state("domcontentloaded", timeout=60000)
                    submit = page.locator("button:has-text('照会'), input[value='照会']")
                    if await submit.count() > 0:
                        await submit.first.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=60000)
                except Exception as e:  # noqa: BLE001 — page layout drift is expected
                    log.warning("year/month selection failed (%s); continuing with default view", e)

                rows: list[list[str]] = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('table tbody tr'))
                        .map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()))"""
                )
                if not rows and await page.locator("select").count() == 0:
                    raise EposLoginChallengeError(
                        "Epos Net history page did not expose expected controls or rows"
                    )

                # Persist cookies / storage so future runs can skip CVV prompt
                STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(STORAGE_STATE_PATH))
            finally:
                await browser.close()

        return rows_to_transactions(rows)
    except EposLoginChallengeError:
        if os.getenv("EPOS_DISABLE_CHROME_APPLESCRIPT_FALLBACK") == "1":
            raise
        log.info("Playwright Epos scrape was challenged; retrying with trusted Chrome AppleScript")
        import asyncio
        return await asyncio.to_thread(_fetch_month_history_with_chrome_apple_events, year, month)
