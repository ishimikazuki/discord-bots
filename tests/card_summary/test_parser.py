from card_summary.parser import parse_epos_email, ParseError
import pytest

def test_parse_normal(fixtures_dir):
    body = (fixtures_dir / "epos_normal.txt").read_text(encoding="utf-8")
    result = parse_epos_email(body, message_id="msg-001")
    assert result.merchant == "SEVEN-ELEVEN/JP TOKYO"
    assert result.amount == 850
    assert result.occurred_at.startswith("2026-05-07T14:23")
    assert result.source == "gmail"
    assert result.source_id == "msg-001"
    assert result.category is None  # categorizer comes later

def test_parse_unknown_format_raises(fixtures_dir):
    with pytest.raises(ParseError):
        parse_epos_email("こんにちは、エポスです。利用情報なし。", message_id="msg-bad")
