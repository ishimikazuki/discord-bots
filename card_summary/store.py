"""SQLite persistence layer for card_summary."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  merchant TEXT NOT NULL,
  amount INTEGER NOT NULL,
  category TEXT,
  source TEXT NOT NULL CHECK (source IN ('gmail', 'epos_net')),
  source_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transactions_occurred_at ON transactions(occurred_at);

CREATE TABLE IF NOT EXISTS category_rules (
  pattern TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('seed', 'llm', 'manual')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS monthly_close (
  year_month TEXT PRIMARY KEY,
  confirmed_amount INTEGER NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_state (
  slot TEXT PRIMARY KEY CHECK (slot IN ('morning', 'afternoon', 'night')),
  last_posted_at TEXT,
  last_total INTEGER,
  last_breakdown_hash TEXT,
  last_max_tx_id INTEGER,
  last_alert_hash TEXT,
  last_thread_id TEXT
);

CREATE TABLE IF NOT EXISTS fetch_checkpoint (
  source TEXT PRIMARY KEY,
  last_fetch_at TEXT NOT NULL
);
"""

def init_db(db_path: Path) -> None:
    """Create tables if they do not exist. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

def open_conn(db_path: Path) -> sqlite3.Connection:
    """Open a connection with row_factory and foreign_keys."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@dataclass(frozen=True)
class Transaction:
    occurred_at: str   # ISO8601
    merchant: str
    amount: int        # 円
    category: str | None
    source: str        # 'gmail' | 'epos_net'
    source_id: str


@contextmanager
def _conn(db_path: Path):
    c = open_conn(db_path)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def upsert_transactions(db_path: Path, txs: list[Transaction]) -> int:
    """INSERT OR IGNORE rows. Returns number of new rows inserted."""
    if not txs:
        return 0
    with _conn(db_path) as c:
        before = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        c.executemany(
            """
            INSERT OR IGNORE INTO transactions
              (occurred_at, merchant, amount, category, source, source_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(t.occurred_at, t.merchant, t.amount, t.category, t.source, t.source_id) for t in txs],
        )
        after = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        return after - before


# fetch_checkpoint -----------------------------------------------------------
def get_fetch_checkpoint(db_path: Path, source: str) -> str | None:
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT last_fetch_at FROM fetch_checkpoint WHERE source = ?", (source,)
        ).fetchone()
        return row["last_fetch_at"] if row else None

def set_fetch_checkpoint(db_path: Path, source: str, when: str) -> None:
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO fetch_checkpoint (source, last_fetch_at) VALUES (?, ?)
            ON CONFLICT(source) DO UPDATE SET last_fetch_at = excluded.last_fetch_at
            """,
            (source, when),
        )

# summary_state --------------------------------------------------------------
def get_summary_state(db_path: Path, slot: str) -> dict | None:
    with _conn(db_path) as c:
        row = c.execute("SELECT * FROM summary_state WHERE slot = ?", (slot,)).fetchone()
        return dict(row) if row else None

def set_summary_state(
    db_path: Path,
    slot: str,
    *,
    last_posted_at: str,
    last_total: int,
    last_breakdown_hash: str,
    last_max_tx_id: int,
    last_alert_hash: str,
    last_thread_id: str,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO summary_state
              (slot, last_posted_at, last_total, last_breakdown_hash,
               last_max_tx_id, last_alert_hash, last_thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slot) DO UPDATE SET
              last_posted_at = excluded.last_posted_at,
              last_total = excluded.last_total,
              last_breakdown_hash = excluded.last_breakdown_hash,
              last_max_tx_id = excluded.last_max_tx_id,
              last_alert_hash = excluded.last_alert_hash,
              last_thread_id = excluded.last_thread_id
            """,
            (slot, last_posted_at, last_total, last_breakdown_hash,
             last_max_tx_id, last_alert_hash, last_thread_id),
        )

# monthly_close --------------------------------------------------------------
def upsert_monthly_close(db_path: Path, year_month: str, confirmed_amount: int, fetched_at: str) -> None:
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO monthly_close (year_month, confirmed_amount, fetched_at) VALUES (?, ?, ?)
            ON CONFLICT(year_month) DO UPDATE SET
              confirmed_amount = excluded.confirmed_amount,
              fetched_at = excluded.fetched_at
            """,
            (year_month, confirmed_amount, fetched_at),
        )

def get_monthly_close(db_path: Path, year_month: str) -> int | None:
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT confirmed_amount FROM monthly_close WHERE year_month = ?", (year_month,)
        ).fetchone()
        return row["confirmed_amount"] if row else None

# category_rules -------------------------------------------------------------
def seed_category_rules(db_path: Path, mapping: dict[str, str]) -> None:
    """Bulk insert seed rules. Won't overwrite existing rules."""
    with _conn(db_path) as c:
        c.executemany(
            "INSERT OR IGNORE INTO category_rules (pattern, category, source) VALUES (?, ?, 'seed')",
            [(k.upper(), v) for k, v in mapping.items()],
        )

def set_category_rule(db_path: Path, pattern: str, category: str, *, source: str) -> None:
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO category_rules (pattern, category, source) VALUES (?, ?, ?)
            ON CONFLICT(pattern) DO UPDATE SET category = excluded.category, source = excluded.source
            """,
            (pattern.upper(), category, source),
        )

def get_category_for(db_path: Path, merchant: str) -> str | None:
    """Return matching category by substring match (case-insensitive)."""
    if not merchant:
        return None
    upper = merchant.upper()
    with _conn(db_path) as c:
        rows = c.execute("SELECT pattern, category FROM category_rules").fetchall()
        for row in rows:
            if row["pattern"] in upper:
                return row["category"]
    return None
