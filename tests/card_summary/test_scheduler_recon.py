from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from card_summary.scheduler import _next_recon_run, run_reconciliation
from card_summary.store import Transaction, init_db, open_conn


def test_next_recon_run_same_day_before_three():
    now = datetime(2026, 5, 7, 2, 30, 0)
    assert _next_recon_run(now) == datetime(2026, 5, 7, 3, 0, 0)


def test_next_recon_run_next_day_after_three():
    now = datetime(2026, 5, 7, 3, 0, 0)
    assert _next_recon_run(now) == datetime(2026, 5, 8, 3, 0, 0)


@pytest.mark.asyncio
async def test_run_reconciliation_fetches_current_and_previous_month(tmp_db):
    init_db(tmp_db)
    fetcher = AsyncMock(return_value=[
        Transaction(
            occurred_at="2026-05-01T00:00:00",
            merchant="AP/サミット",
            amount=1200,
            category=None,
            source="epos_net",
            source_id="epos:test-1",
        )
    ])
    llm = MagicMock(return_value="その他")

    inserted = await run_reconciliation(
        db_path=tmp_db,
        llm_fn=llm,
        fetcher=fetcher,
        now=datetime(2026, 5, 9, 3, 0, 0),
    )

    assert inserted == 1
    assert fetcher.await_args_list[0].args == (2026, 5)
    assert fetcher.await_args_list[1].args == (2026, 4)

    conn = open_conn(tmp_db)
    try:
        rows = conn.execute(
            "SELECT merchant, category, source FROM transactions"
        ).fetchall()
    finally:
        conn.close()
    assert [dict(row) for row in rows] == [
        {"merchant": "AP/サミット", "category": "食費", "source": "epos_net"}
    ]
    llm.assert_not_called()
