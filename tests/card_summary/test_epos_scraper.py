"""Unit tests for the pure parsers in card_summary.epos_scraper.

The async fetch_month_history is not unit-tested here — it depends on
Playwright + a live Epos Net session. Run it manually for end-to-end checks.
"""
from card_summary.epos_scraper import (
    EposLoginChallengeError,
    _parse_amount,
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
