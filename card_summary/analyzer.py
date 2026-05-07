"""Compute monthly aggregations, highlights, and alerts."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path
from card_summary.store import open_conn
from card_summary.config import (
    ALERT_PACE_RATIO, ALERT_CATEGORY_RATIO, ALERT_SINGLE_TX_RATIO,
)

def month_total(db_path: Path, year_month: str) -> int:
    """Sum of amounts in the given YYYY-MM (inclusive)."""
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ?",
            (year_month,),
        ).fetchone()
    return int(row["s"])

def prev_month_same_day_total(db_path: Path, today: datetime) -> int:
    """Sum of prev-month transactions whose day-of-month <= today.day."""
    if today.month == 1:
        prev_year, prev_month = today.year - 1, 12
    else:
        prev_year, prev_month = today.year, today.month - 1
    prev_ym = f"{prev_year:04d}-{prev_month:02d}"
    upper_day = f"{today.day:02d}"
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ? AND substr(occurred_at, 9, 2) <= ?",
            (prev_ym, upper_day),
        ).fetchone()
    return int(row["s"])

def category_breakdown(db_path: Path, year_month: str) -> dict[str, int]:
    """{category: total_amount}. Null categories are bucketed as 'その他'."""
    with open_conn(db_path) as c:
        rows = c.execute(
            "SELECT COALESCE(category, 'その他') AS cat, SUM(amount) AS s "
            "FROM transactions WHERE substr(occurred_at, 1, 7) = ? GROUP BY cat",
            (year_month,),
        ).fetchall()
    return {r["cat"]: int(r["s"]) for r in rows}

def highlight_tx(db_path: Path, year_month: str, since_max_id: int) -> dict | None:
    """Largest tx in the month with id > since_max_id. None if no new tx."""
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT id, occurred_at, merchant, amount FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ? AND id > ? "
            "ORDER BY amount DESC LIMIT 1",
            (year_month, since_max_id),
        ).fetchone()
    return dict(row) if row else None

def max_tx_id(db_path: Path, year_month: str) -> int:
    """Largest transactions.id in the given month. 0 if none."""
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ?",
            (year_month,),
        ).fetchone()
    return int(row["m"])


@dataclass(frozen=True)
class Alert:
    kind: str       # 'pace' | 'category' | 'single'
    message: str

def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_first = datetime(year + 1, 1, 1)
    else:
        next_first = datetime(year, month + 1, 1)
    return (next_first - datetime(year, month, 1)).days

def _prev_month_total(db_path: Path, today: datetime) -> int:
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    return month_total(db_path, f"{prev_y:04d}-{prev_m:02d}")

def _prev_month_same_day_category(db_path: Path, today: datetime) -> dict[str, int]:
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    prev_ym = f"{prev_y:04d}-{prev_m:02d}"
    upper_day = f"{today.day:02d}"
    with open_conn(db_path) as c:
        rows = c.execute(
            "SELECT COALESCE(category, 'その他') AS cat, SUM(amount) AS s "
            "FROM transactions WHERE substr(occurred_at, 1, 7) = ? AND substr(occurred_at, 9, 2) <= ? "
            "GROUP BY cat",
            (prev_ym, upper_day),
        ).fetchall()
    return {r["cat"]: int(r["s"]) for r in rows}

def _last_30_days_amounts(db_path: Path, today: datetime) -> list[int]:
    cutoff = (today - timedelta(days=30)).isoformat()
    with open_conn(db_path) as c:
        rows = c.execute(
            "SELECT amount FROM transactions WHERE occurred_at >= ?",
            (cutoff,),
        ).fetchall()
    return [int(r["amount"]) for r in rows]

def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2

def detect_alerts(db_path: Path, today: datetime) -> list[Alert]:
    """Return list of triggered alerts."""
    alerts: list[Alert] = []
    ym = f"{today.year:04d}-{today.month:02d}"
    this_total = month_total(db_path, ym)
    prev_total = _prev_month_total(db_path, today)

    # 1. pace alert
    if today.day > 0 and prev_total > 0:
        days_in = _days_in_month(today.year, today.month)
        projection = (this_total / today.day) * days_in
        if projection > prev_total * ALERT_PACE_RATIO:
            alerts.append(Alert(
                "pace",
                f"月ペース予測: ¥{int(projection):,} (前月 ¥{prev_total:,})",
            ))

    # 2. category alert
    this_cat = category_breakdown(db_path, ym)
    prev_cat = _prev_month_same_day_category(db_path, today)
    for cat, this_amt in this_cat.items():
        prev_amt = prev_cat.get(cat, 0)
        if prev_amt > 0 and this_amt > prev_amt * ALERT_CATEGORY_RATIO:
            ratio = this_amt / prev_amt * 100
            alerts.append(Alert(
                "category",
                f"{cat} が前月同日比 +{int(ratio - 100)}% (今月ペース注意!)",
            ))

    # 3. single tx alert (largest tx today exceeds 5x of 30-day median)
    recent = _last_30_days_amounts(db_path, today)
    med = _median([a for a in recent if a > 0])
    if med > 0:
        with open_conn(db_path) as c:
            row = c.execute(
                "SELECT merchant, amount FROM transactions "
                "WHERE substr(occurred_at, 1, 10) = ? "
                "ORDER BY amount DESC LIMIT 1",
                (today.date().isoformat(),),
            ).fetchone()
        if row and int(row["amount"]) > med * ALERT_SINGLE_TX_RATIO:
            alerts.append(Alert(
                "single",
                f"⚡ 異常高額: {row['merchant']} ¥{int(row['amount']):,} (普段の{int(row['amount']/med)}倍)",
            ))

    return alerts


@dataclass(frozen=True)
class SummaryReport:
    today: datetime
    year_month: str
    month_total: int
    prev_month_same_day: int
    category_breakdown: dict[str, int]
    highlight: dict | None
    alerts: list[Alert]
    max_tx_id: int
    breakdown_hash: str
    alert_hash: str


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def compute_report(db_path: Path, today: datetime, since_max_id: int) -> SummaryReport:
    ym = f"{today.year:04d}-{today.month:02d}"
    breakdown = category_breakdown(db_path, ym)
    alerts = detect_alerts(db_path, today)
    breakdown_json = json.dumps(breakdown, sort_keys=True, ensure_ascii=False)
    alerts_json = json.dumps([(a.kind, a.message) for a in alerts], ensure_ascii=False)
    return SummaryReport(
        today=today,
        year_month=ym,
        month_total=month_total(db_path, ym),
        prev_month_same_day=prev_month_same_day_total(db_path, today),
        category_breakdown=breakdown,
        highlight=highlight_tx(db_path, ym, since_max_id),
        alerts=alerts,
        max_tx_id=max_tx_id(db_path, ym),
        breakdown_hash=_sha256(breakdown_json),
        alert_hash=_sha256(alerts_json),
    )


def has_changed(prev: dict | None, report: SummaryReport) -> bool:
    """Returns True if any of the 4 tracked values differ from prev."""
    if prev is None:
        return True
    return (
        prev["last_total"] != report.month_total
        or prev["last_breakdown_hash"] != report.breakdown_hash
        or prev["last_max_tx_id"] != report.max_tx_id
        or prev["last_alert_hash"] != report.alert_hash
    )
