from card_summary.store import (
    init_db, get_fetch_checkpoint, set_fetch_checkpoint,
    get_summary_state, set_summary_state,
    upsert_monthly_close, get_monthly_close,
    seed_category_rules, get_category_for, set_category_rule,
)

def test_fetch_checkpoint_roundtrip(tmp_db):
    init_db(tmp_db)
    assert get_fetch_checkpoint(tmp_db, "gmail") is None
    set_fetch_checkpoint(tmp_db, "gmail", "2026-05-07T07:00:00")
    assert get_fetch_checkpoint(tmp_db, "gmail") == "2026-05-07T07:00:00"
    set_fetch_checkpoint(tmp_db, "gmail", "2026-05-07T15:00:00")
    assert get_fetch_checkpoint(tmp_db, "gmail") == "2026-05-07T15:00:00"

def test_summary_state_roundtrip(tmp_db):
    init_db(tmp_db)
    assert get_summary_state(tmp_db, "morning") is None
    set_summary_state(
        tmp_db, "morning",
        last_posted_at="2026-05-07T07:00:00",
        last_total=48200,
        last_breakdown_hash="abc",
        last_max_tx_id=42,
        last_alert_hash="xyz",
        last_thread_id="1234567890",
    )
    s = get_summary_state(tmp_db, "morning")
    assert s["last_total"] == 48200
    assert s["last_thread_id"] == "1234567890"

def test_monthly_close_upsert(tmp_db):
    init_db(tmp_db)
    upsert_monthly_close(tmp_db, "2026-04", 58000, "2026-05-01T03:00:00")
    assert get_monthly_close(tmp_db, "2026-04") == 58000
    upsert_monthly_close(tmp_db, "2026-04", 58500, "2026-05-02T03:00:00")
    assert get_monthly_close(tmp_db, "2026-04") == 58500

def test_category_rules_seed_and_lookup(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {"AMAZON": "ネット通販", "SEVEN-ELEVEN": "コンビニ"})
    assert get_category_for(tmp_db, "amazon.co.jp") == "ネット通販"
    assert get_category_for(tmp_db, "Seven-Eleven 渋谷店") == "コンビニ"
    assert get_category_for(tmp_db, "未知の店") is None

def test_category_rules_set_overrides(tmp_db):
    init_db(tmp_db)
    set_category_rule(tmp_db, "AMAZON", "ネット通販", source="seed")
    set_category_rule(tmp_db, "AMAZON", "通販", source="manual")  # overwrite
    assert get_category_for(tmp_db, "Amazon Prime") == "通販"
