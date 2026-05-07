from datetime import datetime
from card_summary.store import init_db, open_conn, Transaction, upsert_transactions

def make_tx(source_id: str, amount: int = 850, merchant: str = "SEVEN-ELEVEN") -> Transaction:
    return Transaction(
        occurred_at=datetime(2026, 5, 7, 14, 23).isoformat(),
        merchant=merchant,
        amount=amount,
        category=None,
        source="gmail",
        source_id=source_id,
    )

def test_upsert_inserts_new(tmp_db):
    init_db(tmp_db)
    inserted = upsert_transactions(tmp_db, [make_tx("msg-1"), make_tx("msg-2")])
    assert inserted == 2
    with open_conn(tmp_db) as c:
        rows = c.execute("SELECT source_id FROM transactions ORDER BY source_id").fetchall()
        assert [r["source_id"] for r in rows] == ["msg-1", "msg-2"]

def test_upsert_is_idempotent(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [make_tx("msg-1")])
    inserted_again = upsert_transactions(tmp_db, [make_tx("msg-1")])
    assert inserted_again == 0
    with open_conn(tmp_db) as c:
        count = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert count == 1
