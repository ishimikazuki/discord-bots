"""Compute monthly aggregations, highlights, and alerts."""
from __future__ import annotations
from datetime import datetime, date
from pathlib import Path
from card_summary.store import open_conn

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
