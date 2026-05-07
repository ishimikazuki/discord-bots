"""Render SummaryReport into a Discord post body."""
from __future__ import annotations
from card_summary.analyzer import SummaryReport

SLOT_LABELS = {
    "morning": "7:00",
    "afternoon": "15:00",
    "night": "22:00",
}

def format_report(report: SummaryReport, *, slot: str) -> str:
    label = SLOT_LABELS.get(slot, slot)
    date_str = f"{report.today.month}/{report.today.day}"
    diff = report.month_total - report.prev_month_same_day
    diff_pct = (diff / report.prev_month_same_day * 100) if report.prev_month_same_day else 0.0
    sign = "+" if diff >= 0 else ""
    lines = [
        f"🔔 {label} サマリー ({date_str})",
        "─────────────────────",
        f"今月累計: ¥{report.month_total:,}",
        f"　前月同日比: {sign}¥{diff:,} ({sign}{diff_pct:.1f}%)",
        "",
        "📊 カテゴリ別:",
    ]
    if report.category_breakdown:
        for cat, amt in sorted(report.category_breakdown.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {cat:<8} ¥{amt:,}")
    else:
        lines.append("  (なし)")

    lines.append("")
    lines.append("🏆 ハイライト:")
    if report.highlight:
        h = report.highlight
        lines.append(f"  {h['merchant']} ¥{h['amount']:,} ({h['occurred_at'][:16].replace('T', ' ')})")
    else:
        lines.append("  なし")

    lines.append("")
    lines.append("⚠️ アラート:")
    if report.alerts:
        for a in report.alerts:
            lines.append(f"  {a.message}")
    else:
        lines.append("  なし")

    return "\n".join(lines)
