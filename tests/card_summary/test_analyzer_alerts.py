from datetime import datetime
from card_summary.store import init_db, upsert_transactions, Transaction
from card_summary.analyzer import detect_alerts, Alert

def _tx(day: int, amount: int, merchant="X", category=None, month=5, prefix="m"):
    return Transaction(
        occurred_at=datetime(2026, month, day, 12, 0).isoformat(),
        merchant=merchant, amount=amount, category=category,
        source="gmail", source_id=f"{prefix}-{day}-{amount}-{merchant}",
    )

def test_pace_alert_triggers_when_projection_exceeds_threshold(tmp_db):
    init_db(tmp_db)
    # April: ¥30,000 total
    upsert_transactions(tmp_db, [_tx(15, 30000, prefix="apr", month=4)])
    # May 7: ¥10,000 in 7 days → projection = 10000/7*30 = ~42,857
    # threshold: prev_month (30000) * 1.3 = 39,000 → trigger
    upsert_transactions(tmp_db, [_tx(7, 10000, prefix="may")])
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert any(a.kind == "pace" for a in alerts)

def test_pace_alert_does_not_trigger_when_below_threshold(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [_tx(15, 50000, prefix="apr", month=4)])
    upsert_transactions(tmp_db, [_tx(7, 10000, prefix="may")])  # projection ~42,857 < 65,000
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert not any(a.kind == "pace" for a in alerts)

def test_category_alert_triggers_on_2x(tmp_db):
    init_db(tmp_db)
    # prev month same-day food: ¥3,000
    upsert_transactions(tmp_db, [_tx(5, 3000, "A", "食費", month=4, prefix="apr")])
    # this month food: ¥7,000 → 2.33x → trigger
    upsert_transactions(tmp_db, [_tx(5, 7000, "B", "食費", prefix="may")])
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert any(a.kind == "category" and "食費" in a.message for a in alerts)

def test_single_tx_alert_triggers_on_5x_median(tmp_db):
    init_db(tmp_db)
    # 30-day median ~1000, then a 6000 tx → 6x → trigger
    base = [_tx(d, 1000, prefix=f"base{d}") for d in range(1, 8)]
    upsert_transactions(tmp_db, base)
    upsert_transactions(tmp_db, [_tx(7, 6000, "BIG", prefix="big")])
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert any(a.kind == "single" for a in alerts)
