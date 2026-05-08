from card_summary.parser import parse_epos_email, ParseError
import pytest

def test_parse_normal(fixtures_dir):
    body = (fixtures_dir / "epos_normal.txt").read_text(encoding="utf-8")
    result = parse_epos_email(body, message_id="msg-001")
    assert result.merchant == "国内加盟店ショッピング"
    assert result.amount == 1518
    assert result.occurred_at.startswith("2026-05-07T14:23")
    assert result.source == "gmail"
    assert result.source_id == "msg-001"
    assert result.category is None

def test_parse_unknown_format_raises(fixtures_dir):
    with pytest.raises(ParseError):
        parse_epos_email("こんにちは、エポスです。利用情報なし。", message_id="msg-bad")

def test_parse_cancel_negative_amount(fixtures_dir):
    body = (fixtures_dir / "epos_cancel.txt").read_text(encoding="utf-8")
    result = parse_epos_email(body, message_id="msg-002")
    assert result.amount == -3200
    assert result.merchant == "国内加盟店ショッピング"

def test_parse_overseas(fixtures_dir):
    body = (fixtures_dir / "epos_overseas.txt").read_text(encoding="utf-8")
    result = parse_epos_email(body, message_id="msg-003")
    assert result.amount == 3100
    assert result.merchant == "海外加盟店ショッピング"
    assert result.occurred_at.startswith("2026-05-06T22:45")
