"""Unit tests for the pure parsers in card_summary.epos_scraper.

The async fetch_month_history is not unit-tested here — it depends on
Playwright + a live Epos Net session. Run it manually for end-to-end checks.
"""
from card_summary.epos_scraper import (
    EposLoginChallengeError,
    _parse_amount,
    _parse_chrome_history_payload,
    _fetch_month_history_with_chrome_apple_events,
    _parse_date,
    make_source_id,
    rows_to_transactions,
)


def test_parse_amount_simple():
    assert _parse_amount("1,518円") == 1518


def test_login_challenge_error_message_is_actionable():
    assert "image/puzzle verification" in str(EposLoginChallengeError("image/puzzle verification"))


def test_parse_amount_negative():
    assert _parse_amount("-3,200円") == -3200


def test_parse_amount_invalid():
    assert _parse_amount("") is None
    assert _parse_amount("abc") is None


def test_parse_date_simple():
    assert _parse_date("2026/5/1") == "2026-05-01T00:00:00"


def test_parse_date_invalid():
    assert _parse_date("invalid") is None
    assert _parse_date("2026/13/40") is None


def test_make_source_id_is_deterministic():
    a = make_source_id("2026-05-01T00:00:00", "AP/サミット", 8439, 0)
    b = make_source_id("2026-05-01T00:00:00", "AP/サミット", 8439, 0)
    assert a == b
    assert a.startswith("epos:")


def test_make_source_id_distinguishes_index():
    a = make_source_id("2026-05-01T00:00:00", "AP/サミット", 8439, 0)
    b = make_source_id("2026-05-01T00:00:00", "AP/サミット", 8439, 1)
    assert a != b


def test_rows_to_transactions_happy_path():
    rows = [
        ["2026/5/1", "GOOGLE*CLOUD 6ZPPC6", "-", "37円", "1回払い", "2026/6", ""],
        ["2026/5/1", "AP/サミット", "-", "8,439円", "1回払い", "2026/6", ""],
        ["2026/5/4", "AP/UBER *EATS HELP, UBER. COM", "-", "2,125円", "1回払い", "2026/6", ""],
    ]
    txs = rows_to_transactions(rows)
    assert len(txs) == 3
    assert txs[0].merchant == "GOOGLE*CLOUD 6ZPPC6"
    assert txs[0].amount == 37
    assert txs[0].occurred_at == "2026-05-01T00:00:00"
    assert txs[0].source == "epos_net"
    assert txs[0].source_id.startswith("epos:")
    assert txs[1].amount == 8439
    assert txs[2].amount == 2125


def test_rows_to_transactions_skips_invalid_rows():
    rows = [
        ["2026/5/1", "VALID", "-", "100円", "1回払い", "2026/6", ""],
        ["", "EMPTY DATE", "-", "100円", "1回払い", "2026/6", ""],   # bad date
        ["2026/5/1", "BAD AMOUNT", "-", "abc", "1回払い", "2026/6", ""],  # bad amount
        ["2026/5/1", "", "-", "100円", "1回払い", "2026/6", ""],          # blank merchant
        ["only", "two"],  # too few cells
    ]
    txs = rows_to_transactions(rows)
    assert len(txs) == 1
    assert txs[0].merchant == "VALID"


def test_rows_to_transactions_handles_negative_amount():
    rows = [["2026/5/7", "TAKE FROM EXAMPLE", "-", "-3,200円", "1回払い", "2026/6", ""]]
    txs = rows_to_transactions(rows)
    assert len(txs) == 1
    assert txs[0].amount == -3200


def test_rows_to_transactions_source_id_does_not_shift_when_new_row_is_inserted():
    old_rows = [
        ["2026/5/7", "GOOGLE*CLOUD", "-", "2,000円", "1回払い", "2026/6", ""],
        ["2026/5/8", "NOTION LABS", "-", "815円", "1回払い", "2026/6", ""],
    ]
    new_rows = [
        ["2026/5/1", "NEW STORE", "-", "100円", "1回払い", "2026/6", ""],
        *old_rows,
    ]

    old_txs = rows_to_transactions(old_rows)
    new_txs = rows_to_transactions(new_rows)

    assert new_txs[1].source_id == old_txs[0].source_id
    assert new_txs[2].source_id == old_txs[1].source_id


def test_rows_to_transactions_disambiguates_identical_rows():
    rows = [
        ["2026/5/1", "SAME STORE", "-", "100円", "1回払い", "2026/6", ""],
        ["2026/5/1", "SAME STORE", "-", "100円", "1回払い", "2026/6", ""],
    ]

    txs = rows_to_transactions(rows)

    assert txs[0].source_id != txs[1].source_id


def test_parse_chrome_history_payload_returns_transactions():
    payload = (
        '{"ok":true,"rows":[["2026/4/1","ＡＰ／セブンイレブン","－",'
        '"616円","1回払い","2026/5",""]]}'
    )

    txs = _parse_chrome_history_payload(payload)

    assert len(txs) == 1
    assert txs[0].occurred_at == "2026-04-01T00:00:00"
    assert txs[0].merchant == "ＡＰ／セブンイレブン"
    assert txs[0].amount == 616


def test_parse_chrome_history_payload_raises_actionable_error():
    try:
        _parse_chrome_history_payload('{"ok":false,"reason":"image verification required"}')
    except EposLoginChallengeError as e:
        assert "image verification required" in str(e)
    else:
        raise AssertionError("expected EposLoginChallengeError")


def test_chrome_apple_events_fetch_uses_keychain_and_payload(monkeypatch):
    import card_summary.epos_scraper as epos_scraper

    seen_accounts = []

    def fake_credential(account):
        seen_accounts.append(account)
        return f"{account}-secret"

    def fake_run(script, *, timeout=120):
        assert "Google Chrome" in script
        assert "use_history_preload.do" in script
        assert "monthSelectTagsDateMonth" in script
        assert "epos-pass-secret" in script
        return (
            '{"ok":true,"rows":[["2026/5/1","ＧＯＯＧＬＥ＊ＣＬＯＵＤ",'
            '"－","37円","1回払い","2026/6",""]]}'
        )

    monkeypatch.setattr(epos_scraper, "get_credential", fake_credential)
    monkeypatch.setattr(epos_scraper, "_run_osascript", fake_run)

    txs = _fetch_month_history_with_chrome_apple_events(2026, 5)

    assert seen_accounts == ["epos-email", "epos-pass", "epos-cvv"]
    assert len(txs) == 1
    assert txs[0].amount == 37
