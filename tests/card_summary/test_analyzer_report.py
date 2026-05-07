from datetime import datetime
from card_summary.store import init_db, upsert_transactions, Transaction, set_summary_state
from card_summary.analyzer import compute_report, has_changed

def _tx(day, amount, merchant="X", category="その他", month=5, prefix="m"):
    return Transaction(
        occurred_at=datetime(2026, month, day, 12, 0).isoformat(),
        merchant=merchant, amount=amount, category=category,
        source="gmail", source_id=f"{prefix}-{day}-{amount}-{merchant}",
    )

def test_compute_report_assembles_all_fields(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(7, 1000, "AMAZON", "ネット通販"),
        _tx(7, 200, "SEVEN", "コンビニ"),
    ])
    r = compute_report(tmp_db, today=datetime(2026, 5, 7), since_max_id=0)
    assert r.month_total == 1200
    assert r.category_breakdown["ネット通販"] == 1000
    assert r.highlight is not None
    assert r.max_tx_id > 0
    assert isinstance(r.alerts, list)
    assert isinstance(r.breakdown_hash, str) and len(r.breakdown_hash) == 64
    assert isinstance(r.alert_hash, str) and len(r.alert_hash) == 64

def test_has_changed_first_time_is_changed(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [_tx(7, 1000)])
    r = compute_report(tmp_db, today=datetime(2026, 5, 7), since_max_id=0)
    assert has_changed(prev=None, report=r) is True

def test_has_changed_returns_false_when_all_four_match(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [_tx(7, 1000)])
    r = compute_report(tmp_db, today=datetime(2026, 5, 7), since_max_id=0)
    set_summary_state(
        tmp_db, "morning",
        last_posted_at="2026-05-07T07:00:00",
        last_total=r.month_total,
        last_breakdown_hash=r.breakdown_hash,
        last_max_tx_id=r.max_tx_id,
        last_alert_hash=r.alert_hash,
        last_thread_id="abc",
    )
    from card_summary.store import get_summary_state
    prev = get_summary_state(tmp_db, "morning")
    assert has_changed(prev=prev, report=r) is False
