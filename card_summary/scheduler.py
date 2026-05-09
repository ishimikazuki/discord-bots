"""Scheduler: orchestrates one slot's batch and the daily timer loop."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import replace
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
from card_summary.epos_scraper import fetch_month_history
from card_summary.analyzer import compute_report, has_changed
from card_summary.formatter import format_report

log = logging.getLogger(__name__)

# Type aliases ----------------------------------------------------------------
FetchFn = Callable[..., Awaitable[list[tuple[str, str]]]]   # async fetch_new(since) -> [(id, body)]
LlmFn = Callable[[str], str]
PostFn = Callable[[str, str], Awaitable["object"]]          # async (thread_name, body) -> Thread
RegisterFn = Callable[["object", str, str], Awaitable[None]]  # async (thread, slot, summary_text)
EposFetchFn = Callable[[int, int], Awaitable[list]]

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


def _next_recon_run(now: datetime | None = None) -> datetime:
    """Return the next daily Epos reconciliation time (03:00 local/JST)."""
    now = now or datetime.now()
    candidate = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if candidate > now:
        return candidate
    return candidate + timedelta(days=1)


def _current_and_previous_months(now: datetime | None = None) -> list[tuple[int, int]]:
    """Return [(current_year, current_month), (prev_year, prev_month)]."""
    now = now or datetime.now()
    current = (now.year, now.month)
    first_this_month = now.replace(day=1)
    prev_day = first_this_month - timedelta(days=1)
    previous = (prev_day.year, prev_day.month)
    return [current, previous]


def _seconds_until(target: datetime) -> float:
    return max(0.0, (target - datetime.now()).total_seconds())


async def run_reconciliation(
    *,
    db_path: Path = DB_PATH,
    llm_fn: LlmFn,
    fetcher: EposFetchFn = fetch_month_history,
    now: datetime | None = None,
) -> int:
    """Fetch current and previous Epos months, categorize, and upsert rows."""
    init_db(db_path)
    seed_category_rules(db_path, CATEGORY_SEED)

    categorizer = Categorizer(db_path, llm_fn=llm_fn)
    categorized = []
    for year, month in _current_and_previous_months(now):
        log.info("reconciliation: fetching epos history year=%04d month=%02d", year, month)
        txs = await fetcher(year, month)
        for tx in txs:
            category = categorizer.categorize(tx.merchant)
            categorized.append(replace(tx, category=category))

    inserted = upsert_transactions(db_path, categorized)
    set_fetch_checkpoint(db_path, "epos_net", (now or datetime.now()).isoformat())
    log.info(
        "reconciliation complete: fetched=%d inserted=%d",
        len(categorized),
        inserted,
    )
    return inserted


async def _run_slot_loop(
    *,
    fetch_new: FetchFn,
    llm_fn: LlmFn,
    post_to_forum: PostFn,
    register_session: RegisterFn,
    db_path: Path,
) -> None:
    """Long-running daily summary loop. Sleeps until next slot, runs it, repeats."""
    while True:
        slot, next_dt = _next_slot_to_run()
        delay = _seconds_until(next_dt)
        log.info("scheduler: next slot=%s in %.0fs (%s)", slot, delay, next_dt.isoformat())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.info("scheduler slot loop cancelled")
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


async def _run_reconciliation_loop(
    *,
    db_path: Path,
    llm_fn: LlmFn,
) -> None:
    """Long-running Epos reconciliation loop. Runs once per day at 03:00."""
    while True:
        next_dt = _next_recon_run()
        delay = _seconds_until(next_dt)
        log.info("scheduler: next reconciliation in %.0fs (%s)", delay, next_dt.isoformat())
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.info("scheduler reconciliation loop cancelled")
            return
        try:
            await run_reconciliation(db_path=db_path, llm_fn=llm_fn)
        except Exception:
            log.exception("reconciliation crashed")
            await asyncio.sleep(60)  # avoid tight retry loop on persistent failure


async def start_scheduler(
    *,
    fetch_new: FetchFn,
    llm_fn: LlmFn,
    post_to_forum: PostFn,
    register_session: RegisterFn,
    db_path: Path = DB_PATH,
) -> None:
    """Long-running scheduler. Runs posting slots and Epos reconciliation."""
    log.info("scheduler started")
    await asyncio.gather(
        _run_slot_loop(
            fetch_new=fetch_new,
            llm_fn=llm_fn,
            post_to_forum=post_to_forum,
            register_session=register_session,
            db_path=db_path,
        ),
        _run_reconciliation_loop(db_path=db_path, llm_fn=llm_fn),
    )

import json as _json

# Discord glue for bot.py ----------------------------------------------------
async def post_to_kanojo_forum(client, forum_channel_id: int, thread_name: str, body: str):
    """Create a thread in the kanojo forum and return it."""
    import discord
    forum = client.get_channel(forum_channel_id)
    if not isinstance(forum, discord.ForumChannel):
        raise RuntimeError(f"channel {forum_channel_id} is not a ForumChannel: {type(forum)}")
    created = await forum.create_thread(
        name=thread_name,
        content=body,
        auto_archive_duration=1440,
    )
    return created.thread

async def register_kanojo_session(
    sessions_path: Path,
    thread,
    slot: str,
    summary_text: str,
    project_dir: str,
) -> None:
    """Pre-populate sessions.json so bot.py's handle_thread_message picks up the thread.

    Also write the summary to data/card_summary/contexts/{thread_id}.txt so the
    first Claude call gets the summary as background context (since the user's
    question alone won't include it).
    """
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    ctx_path = CONTEXT_DIR / f"{thread.id}.txt"
    ctx_path.write_text(summary_text, encoding="utf-8")

    # Read–modify–write on sessions.json (single-process, no lock needed)
    sessions = {}
    if sessions_path.exists():
        sessions = _json.loads(sessions_path.read_text())
    sessions[str(thread.id)] = {
        "sessionId": None,
        "projectDir": project_dir,
        "workDir": project_dir,
        "worktreePath": None,
        "threadName": thread.name,
        "createdAt": datetime.now().isoformat(),
        "lastUsed": datetime.now().isoformat(),
        "messageCount": 0,
        "kanojo_context_file": str(ctx_path),
        "kanojo_slot": slot,
    }
    sessions_path.write_text(_json.dumps(sessions, indent=2, ensure_ascii=False) + "\n")
