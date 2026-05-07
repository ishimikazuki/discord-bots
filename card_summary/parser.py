"""Parse Epos card 'ご利用のお知らせ' email body into Transaction."""
from __future__ import annotations
import re
from datetime import datetime
from card_summary.store import Transaction

class ParseError(ValueError):
    pass

# Patterns -------------------------------------------------------------------
# Match: 【ご利用日時】2026年5月7日 14時23分
_RE_DATETIME = re.compile(
    r"【ご利用日時】\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{1,2})分"
)
# Match: 【ご利用店舗】SEVEN-ELEVEN/JP TOKYO
_RE_MERCHANT = re.compile(r"【ご利用店舗】\s*(.+?)\s*$", re.MULTILINE)
# Match: 【ご利用金額】850 円  /  1,234 円  /  -500 円 (cancellation)
_RE_AMOUNT = re.compile(r"【ご利用金額】\s*(-?[\d,]+)\s*円")

def parse_epos_email(body: str, *, message_id: str) -> Transaction:
    """Parse one Epos notification mail body. Raises ParseError on unknown format."""
    m_dt = _RE_DATETIME.search(body)
    m_merchant = _RE_MERCHANT.search(body)
    m_amount = _RE_AMOUNT.search(body)
    if not (m_dt and m_merchant and m_amount):
        raise ParseError(f"Could not parse Epos mail (msg={message_id})")
    year, month, day, hour, minute = (int(x) for x in m_dt.groups())
    occurred_at = datetime(year, month, day, hour, minute).isoformat()
    merchant = m_merchant.group(1).strip()
    amount_str = m_amount.group(1).replace(",", "")
    amount = int(amount_str)
    return Transaction(
        occurred_at=occurred_at,
        merchant=merchant,
        amount=amount,
        category=None,
        source="gmail",
        source_id=message_id,
    )
