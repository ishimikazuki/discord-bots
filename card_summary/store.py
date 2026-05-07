"""SQLite persistence layer for card_summary."""
from __future__ import annotations
import sqlite3
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
