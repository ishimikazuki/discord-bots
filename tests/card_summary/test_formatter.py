from datetime import datetime
from card_summary.analyzer import SummaryReport, Alert
from card_summary.formatter import format_report

def make_report() -> SummaryReport:
    return SummaryReport(
        today=datetime(2026, 5, 7, 7, 0),
        year_month="2026-05",
        month_total=48200,
        prev_month_same_day=44700,
        category_breakdown={"食費": 18500, "サブスク": 8000, "コンビニ": 5200},
        highlight={"merchant": "Amazon", "amount": 3200, "occurred_at": "2026-05-06T23:42"},
        alerts=[Alert("category", "食費 が前月同日比 +120% (今月ペース注意!)")],
        max_tx_id=42,
        breakdown_hash="x" * 64,
        alert_hash="y" * 64,
    )

def test_format_contains_total_and_diff(monkeypatch):
    text = format_report(make_report(), slot="morning")
    assert "今月累計" in text
    assert "¥48,200" in text
    assert "+¥3,500" in text or "+3,500" in text
    assert "+7.8%" in text

def test_format_contains_categories():
    text = format_report(make_report(), slot="morning")
    assert "食費" in text and "¥18,500" in text
    assert "サブスク" in text and "¥8,000" in text

def test_format_contains_highlight_and_alerts():
    text = format_report(make_report(), slot="morning")
    assert "Amazon" in text and "¥3,200" in text
    assert "前月同日比 +120%" in text

def test_format_handles_empty_highlight_and_alerts():
    r = make_report()
    r2 = SummaryReport(**{**r.__dict__, "highlight": None, "alerts": []})
    text = format_report(r2, slot="night")
    assert "ハイライト" not in text or "なし" in text  # graceful
    assert "アラート" not in text or "なし" in text
