"""Scheduler: orchestrates one slot's batch and the daily timer loop."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from card_summary.config import (
    DB_PATH, CONTEXT_DIR, GMAIL_QUERY, SLOTS, CATEGORY_SEED, KANOJO_FORUM_CHANNEL_ID,
)
from card_summary.store import (
    init_db, set_fetch_checkpoint, get_fetch_checkpoint,
    upsert_transactions, get_summary_state, set_summary_state, seed_category_rules,
)
from card_summary.parser import parse_epos_email, ParseError
from card_summary.categorizer import Categorizer
from card_summary.analyzer import compute_report, has_changed
from card_summary.formatter import format_report

log = logging.getLogger(__name__)

# Type aliases ----------------------------------------------------------------
FetchFn = Callable[..., Awaitable[list[tuple[str, str]]]]   # async fetch_new(since) -> [(id, body)]
LlmFn = Callable[[str], str]
PostFn = Callable[[str, str], Awaitable["object"]]          # async (thread_name, body) -> Thread
RegisterFn = Callable[["object", str, str], Awaitable[None]]  # async (thread, slot, summary_text)

async def run_slot(
    *,
    slot: str,
    db_path: Path = DB_PATH,
    fetch_new: FetchFn,
    llm_fn: LlmFn,
    post_to_forum: PostFn,
    register_session: RegisterFn,
) -> None:
    """One bath cycle for a slot. Idempotent and safe to retry."""
    init_db(db_path)
    seed_category_rules(db_path, CATEGORY_SEED)

    today = datetime.now()

    # 1. fetch new mails
    since = get_fetch_checkpoint(db_path, "gmail")
    log.info("[%s] fetch since=%s", slot, since)
    raw_mails = await fetch_new(since)

    # 2. parse + categorize + store
    categorizer = Categorizer(db_path, llm_fn=llm_fn)
    txs = []
    for msg_id, body in raw_mails:
        try:
            tx = parse_epos_email(body, message_id=msg_id)
        except ParseError as e:
            log.warning("parse failed: %s", e)
            continue
        category = categorizer.categorize(tx.merchant)
        # rebuild Transaction with category set (frozen dataclass, so use replace)
        from dataclasses import replace
        txs.append(replace(tx, category=category))
    upsert_transactions(db_path, txs)
    set_fetch_checkpoint(db_path, "gmail", today.isoformat())

    # 3. compute report
    prev = get_summary_state(db_path, slot)
    since_max_id = prev["last_max_tx_id"] if prev else 0
    report = compute_report(db_path, today=today, since_max_id=since_max_id)

    # 4. has_changed?
    if not has_changed(prev, report):
        log.info("[%s] no change — silent", slot)
        return

    # 5. post and register
    text = format_report(report, slot=slot)
    label = {"morning": "7:00", "afternoon": "15:00", "night": "22:00"}[slot]
    thread_name = f"🔔 {today.month}/{today.day} {label}"
    thread = await post_to_forum(thread_name, text)
    await register_session(thread, slot, text)

    set_summary_state(
        db_path, slot,
        last_posted_at=today.isoformat(),
        last_total=report.month_total,
        last_breakdown_hash=report.breakdown_hash,
        last_max_tx_id=report.max_tx_id,
        last_alert_hash=report.alert_hash,
        last_thread_id=str(thread.id),
    )
    log.info("[%s] posted thread_id=%s total=%d", slot, thread.id, report.month_total)


def _next_slot_to_run() -> tuple[str, datetime]:
    """Return (slot_name, next_run_datetime) — the soonest upcoming slot."""
    now = datetime.now()
    today_candidates = [
        (slot, now.replace(hour=hour, minute=0, second=0, microsecond=0))
        for slot, hour in SLOTS.items()
    ]
    future = [(s, dt) for s, dt in today_candidates if dt > now]
    if future:
        future.sort(key=lambda kv: kv[1])
        return future[0]
    # All today's slots elapsed → first slot of tomorrow
    tomorrow = now + timedelta(days=1)
    first_slot = min(SLOTS, key=SLOTS.get)
    return (first_slot, tomorrow.replace(hour=SLOTS[first_slot], minute=0, second=0, microsecond=0))


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now()).total_seconds())


async def start_scheduler(
    *,
    fetch_new: FetchFn,
    llm_fn: LlmFn,
    post_to_forum: PostFn,
    register_session: RegisterFn,
    db_path: Path = DB_PATH,
) -> None:
    """Long-running daily loop. Sleeps until next slot, runs it, repeats."""
    log.info("scheduler started")
    while True:
        slot, next_dt = _next_slot_to_run()
        delay = _seconds_until(next_dt)
        log.info("scheduler: next slot=%s in %.0fs (%s)", slot, delay, next_dt.isoformat())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.info("scheduler cancelled")
            return
        try:
            await run_slot(
                slot=slot, db_path=db_path,
                fetch_new=fetch_new, llm_fn=llm_fn,
                post_to_forum=post_to_forum, register_session=register_session,
            )
        except Exception:
            log.exception("run_slot crashed for slot=%s", slot)
            await asyncio.sleep(60)  # avoid tight retry loop on persistent failure
