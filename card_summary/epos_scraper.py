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
import logging
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
    for idx, cells in enumerate(rows):
        if len(cells) < 4:
            continue
        occurred_at = _parse_date(cells[0])
        if occurred_at is None:
            continue
        merchant = cells[1].strip()
        amount = _parse_amount(cells[3])
        if amount is None or not merchant:
            continue
        out.append(Transaction(
            occurred_at=occurred_at,
            merchant=merchant,
            amount=amount,
            category=None,
            source="epos_net",
            source_id=make_source_id(occurred_at, merchant, amount, idx),
        ))
    return out


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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
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
        try:
            await page.locator("select").first.wait_for(timeout=5000)
            await page.select_option("select >> nth=0", str(year))
            await page.select_option("select >> nth=1", str(month))
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

        # Persist cookies / storage so future runs can skip CVV prompt
        STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(STORAGE_STATE_PATH))
        await browser.close()

    return rows_to_transactions(rows)
