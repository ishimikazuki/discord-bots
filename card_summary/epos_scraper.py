"""Scrape Epos Net 月別ご利用履歴照会 for transaction data.

Login flow:
  1. Open Epos Net in the user's trusted Google Chrome profile.
  2. POST id/password to login_preload.do when the session is not already trusted.
  3. (occasionally) ご本人様確認: enter 3-digit CVV via on-screen keypad.
  4. GET use_history_preload.do, select year/month, parse table.

Credentials come from macOS keychain (account=epos-email|epos-pass|epos-cvv,
service=epos-net). We intentionally use the existing Chrome profile instead of
a fresh automation browser because Epos Net challenges untrusted sessions.
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from card_summary.store import Transaction

log = logging.getLogger(__name__)

EPOS_LOGIN_URL = "https://www.eposcard.co.jp/memberservice/pc/login/login_preload.do"
EPOS_HISTORY_URL = (
    "https://www.eposcard.co.jp/memberservice/pc/usehistoryreference/use_history_preload.do"
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
  const targetYear = {json.dumps(str(year))};
  const targetMonth = {json.dumps(f"{month:02d}")};
  if (yearSelect.value === targetYear && monthSelect.value === targetMonth) {{
    return JSON.stringify({{ok:true, action:'history-month-already-selected', year:yearSelect.value, month:monthSelect.value}});
  }}
  yearSelect.value = targetYear;
  monthSelect.value = targetMonth;
  if (yearSelect.value !== targetYear || monthSelect.value !== targetMonth) {{
    return JSON.stringify({{ok:false, reason:'target month option missing', year:targetYear, month:targetMonth}});
  }}
  yearSelect.dispatchEvent(new Event('input', {{bubbles:true}}));
  monthSelect.dispatchEvent(new Event('input', {{bubbles:true}}));
  monthSelect.dispatchEvent(new Event('change', {{bubbles:true}}));
  return JSON.stringify({{ok:true, action:'selected-history-month', year:yearSelect.value, month:monthSelect.value}});
}})()
"""
    extract_js = """
(() => {
  const body = document.body ? document.body.innerText : '';
  if (body.includes('通信エラーが発生しました') || body.includes('もう一度ログイン')) {
    return JSON.stringify({ok:false, reason:'login session rejected by Epos Net'});
  }
  const rows = [];
  for (const tr of Array.from(document.querySelectorAll('tr'))) {
    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim());
    if (cells.length >= 4 && /^\\d{4}\\/\\d{1,2}\\/\\d{1,2}$/.test(cells[0])) {
      rows.push(cells);
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
    """Return Epos Net 月別ご利用履歴 through the trusted Chrome profile.

    `headless` is accepted for backwards compatibility with older callers, but
    ignored: this path deliberately uses the user's normal Chrome profile so
    Epos Net sees the same trusted session the user already uses.
    """
    del headless
    import asyncio

    return await asyncio.to_thread(_fetch_month_history_with_chrome_apple_events, year, month)
