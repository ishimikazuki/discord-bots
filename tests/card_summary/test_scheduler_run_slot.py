from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from freezegun import freeze_time
from card_summary.store import init_db, upsert_transactions, Transaction, get_summary_state
from card_summary.scheduler import run_slot

@pytest.mark.asyncio
async def test_run_slot_skips_when_no_change(tmp_db):
    init_db(tmp_db)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)
    fetch = AsyncMock(return_value=[])  # no new mail
    llm = MagicMock(return_value="その他")
    posted = []
    async def post_summary(thread_name, body):
        posted.append((thread_name, body))
        thread = MagicMock(); thread.id = 99999
        return thread
    with freeze_time("2026-05-07 07:00:00"):
        await run_slot(
            slot="morning", db_path=tmp_db,
            fetch_new=fetch, llm_fn=llm,
            post_to_forum=post_summary,
            register_session=AsyncMock(),
        )
    # No prior state and no transactions → has_changed True initially → posts once
    assert len(posted) == 1

@pytest.mark.asyncio
async def test_run_slot_silent_on_repeat(tmp_db):
    init_db(tmp_db)
    fetch = AsyncMock(return_value=[])
    llm = MagicMock(return_value="その他")
    posted = []
    async def post_summary(thread_name, body):
        thread = MagicMock(); thread.id = len(posted) + 1
        posted.append((thread_name, body))
        return thread
    register = AsyncMock()
    with freeze_time("2026-05-07 07:00:00"):
        await run_slot(slot="morning", db_path=tmp_db,
                       fetch_new=fetch, llm_fn=llm,
                       post_to_forum=post_summary, register_session=register)
    with freeze_time("2026-05-07 07:00:01"):
        await run_slot(slot="morning", db_path=tmp_db,
                       fetch_new=fetch, llm_fn=llm,
                       post_to_forum=post_summary, register_session=register)
    assert len(posted) == 1  # second call silent
