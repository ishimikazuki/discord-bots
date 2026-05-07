from datetime import datetime
from card_summary.store import init_db, upsert_transactions, Transaction
from card_summary.analyzer import (
    month_total, prev_month_same_day_total, category_breakdown, highlight_tx,
)

def _tx(day: int, amount: int, merchant: str = "X", category: str | None = None,
        month: int = 5, source_id_prefix: str = "may") -> Transaction:
    return Transaction(
        occurred_at=datetime(2026, month, day, 12, 0).isoformat(),
        merchant=merchant, amount=amount, category=category,
        source="gmail", source_id=f"{source_id_prefix}-{merchant}-{day}-{amount}",
    )

def test_month_total_sums_only_target_month(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, source_id_prefix="may"),
        _tx(2, 2000, source_id_prefix="may"),
        _tx(15, 500, month=4, source_id_prefix="apr"),
    ])
    assert month_total(tmp_db, "2026-05") == 3000
    assert month_total(tmp_db, "2026-04") == 500

def test_prev_month_same_day_total(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, source_id_prefix="apr-1", month=4),
        _tx(7, 4000, source_id_prefix="apr-7", month=4),
        _tx(8, 9999, source_id_prefix="apr-8", month=4),  # past today's day-7 → excluded
        _tx(7, 200, source_id_prefix="may-7"),
    ])
    # As of 2026-05-07, prev-month-same-day total = april 1-7 = 5000
    assert prev_month_same_day_total(tmp_db, today=datetime(2026, 5, 7)) == 5000

def test_category_breakdown(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, "AMAZON", "ネット通販"),
        _tx(2, 500, "SEVEN", "コンビニ"),
        _tx(3, 700, "AMAZON", "ネット通販"),
        _tx(4, 300, "UNKNOWN", None),
    ])
    breakdown = category_breakdown(tmp_db, "2026-05")
    assert breakdown == {"ネット通販": 1700, "コンビニ": 500, "その他": 300}

def test_highlight_tx_returns_max_since_id(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, "X1", source_id_prefix="a"),
        _tx(2, 5000, "X2", source_id_prefix="b"),
        _tx(3, 800, "X3", source_id_prefix="c"),
    ])
    # We want the max-amount tx with id > 1 (i.e. exclude the first)
    h = highlight_tx(tmp_db, "2026-05", since_max_id=1)
    assert h is not None
    assert h["amount"] == 5000
    assert h["merchant"] == "X2"

def test_highlight_tx_none_when_no_new(tmp_db):
    init_db(tmp_db)
    assert highlight_tx(tmp_db, "2026-05", since_max_id=999) is None
