# Card Summary Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build kanojo bot that posts Epos card spend summaries to Discord 3x/day and lets かーくん reply in-thread with full context preserved across the Claude Code session.

**Architecture:** New `card_summary/` package alongside existing `bot.py`. Scheduler runs as asyncio task inside the kanojo bot process. SQLite (`data/card.sqlite3`) is the single source of truth for transactions, category rules, and summary state. Gmail API (direct, not MCP) fetches Epos notification mails. Existing `is_thread + is_our_channel + sessions.json` logic in bot.py already routes thread replies to `handle_thread_message` — we only need to (1) start the scheduler in `on_ready` and (2) inject a context-file into the first Claude call so the model has the summary content as background.

**Tech Stack:** Python 3.13, discord.py 2.x, sqlite3, google-api-python-client, freezegun (test), pytest.

**Spec:** [`docs/superpowers/specs/2026-05-07-card-summary-bot-design.md`](../specs/2026-05-07-card-summary-bot-design.md)

**Important deviation from spec § 10:** The existing `bot.py` (line 858-890) already routes thread messages in the kanojo forum to `handle_thread_message` without requiring a mention. So the "mention緩和"改修 is NOT needed. Instead, we add **context-file injection** (Task 16) to give Claude the summary as background on the first message of a kanojo-posted thread.

---

## File Map

| Path | Purpose | Created/Modified |
|---|---|---|
| `card_summary/__init__.py` | Package marker | Create |
| `card_summary/config.py` | Constants (channel IDs, thresholds, category seed) | Create |
| `card_summary/store.py` | SQLite layer (transactions / category_rules / summary_state / monthly_close / fetch_checkpoint) | Create |
| `card_summary/parser.py` | Epos email body → Transaction | Create |
| `card_summary/categorizer.py` | Merchant → category (dict + LLM fallback) | Create |
| `card_summary/analyzer.py` | Aggregations + alerts + has_changed | Create |
| `card_summary/formatter.py` | SummaryReport → Discord message text | Create |
| `card_summary/gmail_fetcher.py` | Gmail API client (OAuth + fetch_new_since) | Create |
| `card_summary/scheduler.py` | run_slot() + start_scheduler() asyncio loop | Create |
| `card_summary/reconciler.py` | Monthly browser-use reconciliation (optional, Task 20) | Create |
| `bot.py` | Add scheduler startup + context-file injection (2 small edits) | Modify |
| `config.json` | Add `kanojo` bot entry | Modify |
| `requirements.txt` | Add gmail/freezegun deps | Modify |
| `.gitignore` | Ignore data/, gmail_token.json | Modify |
| `tests/card_summary/*` | Unit + integration tests | Create |
| `data/card.sqlite3` | DB (auto-created at runtime) | Runtime |
| `data/card_summary/contexts/{thread_id}.txt` | Per-thread context for first Claude call | Runtime |

---

## Slot Naming Convention (used everywhere)

- `'morning'` = 7:00 JST
- `'afternoon'` = 15:00 JST
- `'night'` = 22:00 JST

DB CHECK constraints, scheduler triggers, and tests all use these three exact strings.

---

## Task 1: Package skeleton, dependencies, gitignore

**Files:**
- Create: `card_summary/__init__.py`
- Create: `card_summary/config.py`
- Create: `tests/card_summary/__init__.py`
- Create: `tests/card_summary/conftest.py`
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: Create package directories and empty markers**

```bash
cd ~/discord-bots
mkdir -p card_summary tests/card_summary tests/card_summary/fixtures data/card_summary/contexts
touch card_summary/__init__.py tests/card_summary/__init__.py
```

- [ ] **Step 2: Write `card_summary/config.py`**

```python
"""Constants for the card_summary package. Edit before deploying."""
from __future__ import annotations
from pathlib import Path

# Discord ---------------------------------------------------------------------
# Set during deployment (Task 18). Use 0 as placeholder to surface mistakes early.
KANOJO_FORUM_CHANNEL_ID: int = 0  # forum channel under kanojo bot

# DB --------------------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "card.sqlite3"
CONTEXT_DIR = Path(__file__).resolve().parent.parent / "data" / "card_summary" / "contexts"

# Gmail -----------------------------------------------------------------------
GMAIL_QUERY = "from:eposcard@eposcard.co.jp"
GMAIL_TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / "gmail_token.json"
GMAIL_CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "data" / "gmail_credentials.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Slots -----------------------------------------------------------------------
SLOTS = {
    "morning": 7,    # hour (JST)
    "afternoon": 15,
    "night": 22,
}

# Alerts ----------------------------------------------------------------------
ALERT_PACE_RATIO = 1.3       # projected month total > prev month × this → alert
ALERT_CATEGORY_RATIO = 2.0   # category total > prev-month-same-day category × this → alert
ALERT_SINGLE_TX_RATIO = 5.0  # single tx > 30-day median × this → alert

# Categories ------------------------------------------------------------------
CATEGORIES = ["食費", "交通", "サブスク", "コンビニ", "ネット通販", "衣料", "医療", "その他"]

CATEGORY_SEED: dict[str, str] = {
    "AMAZON":           "ネット通販",
    "RAKUTEN":          "ネット通販",
    "YAHOO":            "ネット通販",
    "SEVEN-ELEVEN":     "コンビニ",
    "FAMILYMART":       "コンビニ",
    "LAWSON":           "コンビニ",
    "SUICA":            "交通",
    "JR EAST":          "交通",
    "JR-EAST":          "交通",
    "PASMO":            "交通",
    "NETFLIX":          "サブスク",
    "SPOTIFY":          "サブスク",
    "OPENAI":           "サブスク",
    "ANTHROPIC":        "サブスク",
    "UBER":             "食費",
    "DEMAE-CAN":        "食費",
}
```

- [ ] **Step 3: Update `requirements.txt`**

Read the current file first, then append:

```
google-api-python-client>=2.100
google-auth>=2.20
google-auth-oauthlib>=1.0
google-auth-httplib2>=0.2
freezegun>=1.4
pytest>=8.0
```

- [ ] **Step 4: Update `.gitignore`**

Append:

```
# card_summary
data/card.sqlite3
data/card.sqlite3-journal
data/gmail_token.json
data/gmail_credentials.json
data/card_summary/contexts/
```

- [ ] **Step 5: Write `tests/card_summary/conftest.py`**

```python
"""Shared pytest fixtures for card_summary tests."""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pytest

@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Returns a path to an isolated DB file under tmp_path."""
    return tmp_path / "card.sqlite3"

@pytest.fixture
def conn(tmp_db: Path):
    """Open a fresh sqlite connection to tmp_db. Caller is responsible for migrations."""
    c = sqlite3.connect(tmp_db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()

@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
```

- [ ] **Step 6: Install deps and verify**

```bash
cd ~/discord-bots
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/card_summary/ --collect-only
```

Expected: `no tests collected` (no test files yet) — confirms imports/conftest do not crash.

- [ ] **Step 7: Commit**

```bash
git add card_summary/ tests/card_summary/ requirements.txt .gitignore
git commit -m "新規機能: card_summary パッケージ骨組みと依存関係を追加"
```

---

## Task 2: SQLite schema and Connection helper

**Files:**
- Create: `card_summary/store.py`
- Create: `tests/card_summary/test_store_schema.py`

- [ ] **Step 1: Write failing schema test**

`tests/card_summary/test_store_schema.py`:
```python
import sqlite3
from card_summary.store import init_db

def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == {
        "transactions", "category_rules", "monthly_close",
        "summary_state", "fetch_checkpoint",
    }
    conn.close()

def test_init_db_is_idempotent(tmp_db):
    init_db(tmp_db)
    init_db(tmp_db)  # should not raise
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd ~/discord-bots && .venv/bin/pytest tests/card_summary/test_store_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'card_summary.store'`

- [ ] **Step 3: Write `card_summary/store.py` minimal**

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_store_schema.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/store.py tests/card_summary/test_store_schema.py
git commit -m "新規機能: card_summary に SQLite スキーマと init_db を追加"
```

---

## Task 3: Transaction dataclass + upsert (idempotent)

**Files:**
- Modify: `card_summary/store.py`
- Create: `tests/card_summary/test_store_transactions.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_store_transactions.py`:
```python
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
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_store_transactions.py -v`
Expected: FAIL — `ImportError: cannot import name 'Transaction'`

- [ ] **Step 3: Add Transaction dataclass and upsert to `card_summary/store.py`**

Append to `card_summary/store.py`:
```python
from dataclasses import dataclass
from contextlib import contextmanager

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

# Replace open_conn's prior usage in tests by making it a context manager too:
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
```

Also adjust `open_conn` to remain a plain (non-context) connection opener; tests use it via `with` because sqlite3.Connection supports the context-manager protocol. Update the test file to use `with open_conn(tmp_db) as c:` (already written that way).

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_store_transactions.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/store.py tests/card_summary/test_store_transactions.py
git commit -m "新規機能: store に Transaction と upsert_transactions を追加"
```

---

## Task 4: store: fetch_checkpoint, summary_state, monthly_close, category_rules

**Files:**
- Modify: `card_summary/store.py`
- Create: `tests/card_summary/test_store_state.py`

- [ ] **Step 1: Write failing tests**

`tests/card_summary/test_store_state.py`:
```python
from card_summary.store import (
    init_db, get_fetch_checkpoint, set_fetch_checkpoint,
    get_summary_state, set_summary_state,
    upsert_monthly_close, get_monthly_close,
    seed_category_rules, get_category_for, set_category_rule,
)

def test_fetch_checkpoint_roundtrip(tmp_db):
    init_db(tmp_db)
    assert get_fetch_checkpoint(tmp_db, "gmail") is None
    set_fetch_checkpoint(tmp_db, "gmail", "2026-05-07T07:00:00")
    assert get_fetch_checkpoint(tmp_db, "gmail") == "2026-05-07T07:00:00"
    set_fetch_checkpoint(tmp_db, "gmail", "2026-05-07T15:00:00")
    assert get_fetch_checkpoint(tmp_db, "gmail") == "2026-05-07T15:00:00"

def test_summary_state_roundtrip(tmp_db):
    init_db(tmp_db)
    assert get_summary_state(tmp_db, "morning") is None
    set_summary_state(
        tmp_db, "morning",
        last_posted_at="2026-05-07T07:00:00",
        last_total=48200,
        last_breakdown_hash="abc",
        last_max_tx_id=42,
        last_alert_hash="xyz",
        last_thread_id="1234567890",
    )
    s = get_summary_state(tmp_db, "morning")
    assert s["last_total"] == 48200
    assert s["last_thread_id"] == "1234567890"

def test_monthly_close_upsert(tmp_db):
    init_db(tmp_db)
    upsert_monthly_close(tmp_db, "2026-04", 58000, "2026-05-01T03:00:00")
    assert get_monthly_close(tmp_db, "2026-04") == 58000
    upsert_monthly_close(tmp_db, "2026-04", 58500, "2026-05-02T03:00:00")
    assert get_monthly_close(tmp_db, "2026-04") == 58500

def test_category_rules_seed_and_lookup(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {"AMAZON": "ネット通販", "SEVEN-ELEVEN": "コンビニ"})
    assert get_category_for(tmp_db, "amazon.co.jp") == "ネット通販"
    assert get_category_for(tmp_db, "Seven-Eleven 渋谷店") == "コンビニ"
    assert get_category_for(tmp_db, "未知の店") is None

def test_category_rules_set_overrides(tmp_db):
    init_db(tmp_db)
    set_category_rule(tmp_db, "AMAZON", "ネット通販", source="seed")
    set_category_rule(tmp_db, "AMAZON", "通販", source="manual")  # overwrite
    assert get_category_for(tmp_db, "Amazon Prime") == "通販"
```

- [ ] **Step 2: Run to verify failures**

Run: `.venv/bin/pytest tests/card_summary/test_store_state.py -v`
Expected: 5 failures (ImportError on each function).

- [ ] **Step 3: Add the functions to `card_summary/store.py`**

Append:
```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_store_state.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/store.py tests/card_summary/test_store_state.py
git commit -m "新規機能: store に fetch_checkpoint / summary_state / monthly_close / category_rules を追加"
```

---

## Task 5: parser.py for normal Epos email

**Files:**
- Create: `card_summary/parser.py`
- Create: `tests/card_summary/fixtures/epos_normal.txt`
- Create: `tests/card_summary/test_parser.py`

> **Note:** Before implementing, log into Gmail and copy 3 actual Epos "ご利用のお知らせ" mail bodies (with personal info redacted: replace card number with `XXXX-XXXX-XXXX-1234`, replace any names with `XXX`). Save them as `epos_normal.txt`, `epos_cancel.txt`, `epos_overseas.txt` in the fixtures dir. The parser test below uses `epos_normal.txt`. **If you don't have access to a real fixture yet, use the placeholder body below — it matches the format Epos uses circa 2025-2026 (verified from public examples).**

- [ ] **Step 1: Create fixture `tests/card_summary/fixtures/epos_normal.txt`**

```
エポスカードご利用のお知らせ

平素は弊社カードをご利用いただき、誠にありがとうございます。
下記のとおり、カードがご利用されました。

【ご利用日時】2026年5月7日 14時23分
【ご利用店舗】SEVEN-ELEVEN/JP TOKYO
【ご利用金額】850 円
【お支払方法】1回払い
【カード番号】XXXX-XXXX-XXXX-1234

このメールに身に覚えのない場合は下記までご連絡ください。
エポスカード盗難紛失受付センター
0120-XXX-XXX
```

- [ ] **Step 2: Write failing parser test**

`tests/card_summary/test_parser.py`:
```python
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
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'card_summary.parser'`

- [ ] **Step 4: Write `card_summary/parser.py`**

```python
"""Parse Epos card 'ご利用のお知らせ' email body into Transaction."""
from __future__ import annotations
import re
from datetime import datetime
from card_summary.store import Transaction

class ParseError(ValueError):
    pass

# Patterns -------------------------------------------------------------------
# Match: 【ご利用日時】2026年5月7日 14時23分
_RE_DATETIME = re.compile(
    r"【ご利用日時】\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{1,2})分"
)
# Match: 【ご利用店舗】SEVEN-ELEVEN/JP TOKYO
_RE_MERCHANT = re.compile(r"【ご利用店舗】\s*(.+?)\s*$", re.MULTILINE)
# Match: 【ご利用金額】850 円  /  1,234 円  /  -500 円 (cancellation)
_RE_AMOUNT = re.compile(r"【ご利用金額】\s*(-?[\d,]+)\s*円")

def parse_epos_email(body: str, *, message_id: str) -> Transaction:
    """Parse one Epos notification mail body. Raises ParseError on unknown format."""
    m_dt = _RE_DATETIME.search(body)
    m_merchant = _RE_MERCHANT.search(body)
    m_amount = _RE_AMOUNT.search(body)
    if not (m_dt and m_merchant and m_amount):
        raise ParseError(f"Could not parse Epos mail (msg={message_id})")
    year, month, day, hour, minute = (int(x) for x in m_dt.groups())
    occurred_at = datetime(year, month, day, hour, minute).isoformat()
    merchant = m_merchant.group(1).strip()
    amount_str = m_amount.group(1).replace(",", "")
    amount = int(amount_str)
    return Transaction(
        occurred_at=occurred_at,
        merchant=merchant,
        amount=amount,
        category=None,
        source="gmail",
        source_id=message_id,
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_parser.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add card_summary/parser.py tests/card_summary/test_parser.py tests/card_summary/fixtures/epos_normal.txt
git commit -m "新規機能: エポス通常利用メール parser を追加"
```

---

## Task 6: parser cancel + overseas variants

**Files:**
- Create: `tests/card_summary/fixtures/epos_cancel.txt`
- Create: `tests/card_summary/fixtures/epos_overseas.txt`
- Modify: `tests/card_summary/test_parser.py`

- [ ] **Step 1: Create fixtures**

`tests/card_summary/fixtures/epos_cancel.txt`:
```
エポスカードご利用のお知らせ（取消）

下記のとおり、カードご利用が取り消されました。

【ご利用日時】2026年5月7日 10時00分
【ご利用店舗】AMAZON.CO.JP
【ご利用金額】-3,200 円
【お支払方法】1回払い
【カード番号】XXXX-XXXX-XXXX-1234
```

`tests/card_summary/fixtures/epos_overseas.txt`:
```
エポスカード海外ご利用のお知らせ

下記のとおり、カードがご利用されました。

【ご利用日時】2026年5月6日 22時45分
【ご利用店舗】OPENAI*CHATGPT/SAN FRANCISCO US
【ご利用金額】3,100 円
【お支払方法】1回払い
【カード番号】XXXX-XXXX-XXXX-1234
```

- [ ] **Step 2: Add tests**

Append to `tests/card_summary/test_parser.py`:
```python
def test_parse_cancel_negative_amount(fixtures_dir):
    body = (fixtures_dir / "epos_cancel.txt").read_text(encoding="utf-8")
    result = parse_epos_email(body, message_id="msg-002")
    assert result.amount == -3200
    assert result.merchant == "AMAZON.CO.JP"

def test_parse_overseas(fixtures_dir):
    body = (fixtures_dir / "epos_overseas.txt").read_text(encoding="utf-8")
    result = parse_epos_email(body, message_id="msg-003")
    assert result.amount == 3100
    assert "OPENAI" in result.merchant
    assert result.occurred_at.startswith("2026-05-06T22:45")
```

- [ ] **Step 3: Run to verify pass (parser already handles -, comma)**

Run: `.venv/bin/pytest tests/card_summary/test_parser.py -v`
Expected: PASS (4 tests). The parser written in Task 5 already handles negative + comma-separated amounts; these fixtures verify that.

- [ ] **Step 4: Commit**

```bash
git add tests/card_summary/fixtures/epos_cancel.txt tests/card_summary/fixtures/epos_overseas.txt tests/card_summary/test_parser.py
git commit -m "テスト: parser にキャンセル/海外利用ケースを追加"
```

---

## Task 7: categorizer.py

**Files:**
- Create: `card_summary/categorizer.py`
- Create: `tests/card_summary/test_categorizer.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_categorizer.py`:
```python
from unittest.mock import Mock
from card_summary.store import init_db, seed_category_rules, get_category_for
from card_summary.categorizer import Categorizer

def test_dict_hit_returns_category_without_calling_llm(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {"AMAZON": "ネット通販"})
    llm = Mock(side_effect=AssertionError("LLM must not be called for dict hit"))
    cat = Categorizer(tmp_db, llm_fn=llm)
    assert cat.categorize("Amazon.co.jp") == "ネット通販"
    llm.assert_not_called()

def test_dict_miss_calls_llm_and_caches(tmp_db):
    init_db(tmp_db)
    seed_category_rules(tmp_db, {})  # empty dict
    llm = Mock(return_value="食費")
    cat = Categorizer(tmp_db, llm_fn=llm)
    result = cat.categorize("ZENIYA RAMEN")
    assert result == "食費"
    llm.assert_called_once_with("ZENIYA RAMEN")
    # Subsequent call should hit the learned rule, not LLM
    llm.reset_mock()
    assert cat.categorize("Zeniya Ramen 渋谷店") == "食費"
    llm.assert_not_called()

def test_llm_failure_returns_none(tmp_db):
    init_db(tmp_db)
    llm = Mock(side_effect=RuntimeError("LLM down"))
    cat = Categorizer(tmp_db, llm_fn=llm)
    assert cat.categorize("UNKNOWN MERCHANT") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_categorizer.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `card_summary/categorizer.py`**

```python
"""Merchant → category resolver. Dictionary first, LLM fallback."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Callable
from card_summary.store import get_category_for, set_category_rule
from card_summary.config import CATEGORIES

log = logging.getLogger(__name__)

LlmFn = Callable[[str], str]

class Categorizer:
    def __init__(self, db_path: Path, llm_fn: LlmFn):
        self.db_path = db_path
        self.llm_fn = llm_fn

    def categorize(self, merchant: str) -> str | None:
        if not merchant:
            return None
        # 1. dictionary lookup
        hit = get_category_for(self.db_path, merchant)
        if hit:
            return hit
        # 2. LLM fallback
        try:
            result = self.llm_fn(merchant)
        except Exception as e:
            log.warning("LLM categorize failed for %r: %s", merchant, e)
            return None
        if result not in CATEGORIES:
            log.warning("LLM returned invalid category %r for %r", result, merchant)
            return None
        # 3. learn (use upper-cased merchant as the pattern; substring match handles variants)
        set_category_rule(self.db_path, merchant.upper(), result, source="llm")
        return result
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_categorizer.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/categorizer.py tests/card_summary/test_categorizer.py
git commit -m "新規機能: categorizer (辞書 + LLM フォールバック + 学習)"
```

---

## Task 8: analyzer.py — aggregations

**Files:**
- Create: `card_summary/analyzer.py`
- Create: `tests/card_summary/test_analyzer_aggregations.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_analyzer_aggregations.py`:
```python
from datetime import datetime
from card_summary.store import init_db, upsert_transactions, Transaction
from card_summary.analyzer import (
    month_total, prev_month_same_day_total, category_breakdown, highlight_tx,
)

def _tx(day: int, amount: int, merchant: str = "X", category: str | None = None,
        month: int = 5, source_id_prefix: str = "may") -> Transaction:
    return Transaction(
        occurred_at=datetime(2026, month, day, 12, 0).isoformat(),
        merchant=merchant, amount=amount, category=category,
        source="gmail", source_id=f"{source_id_prefix}-{merchant}-{day}-{amount}",
    )

def test_month_total_sums_only_target_month(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, source_id_prefix="may"),
        _tx(2, 2000, source_id_prefix="may"),
        _tx(15, 500, month=4, source_id_prefix="apr"),
    ])
    assert month_total(tmp_db, "2026-05") == 3000
    assert month_total(tmp_db, "2026-04") == 500

def test_prev_month_same_day_total(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, source_id_prefix="apr-1", month=4),
        _tx(7, 4000, source_id_prefix="apr-7", month=4),
        _tx(8, 9999, source_id_prefix="apr-8", month=4),  # past today's day-7 → excluded
        _tx(7, 200, source_id_prefix="may-7"),
    ])
    # As of 2026-05-07, prev-month-same-day total = april 1-7 = 5000
    assert prev_month_same_day_total(tmp_db, today=datetime(2026, 5, 7)) == 5000

def test_category_breakdown(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, "AMAZON", "ネット通販"),
        _tx(2, 500, "SEVEN", "コンビニ"),
        _tx(3, 700, "AMAZON", "ネット通販"),
        _tx(4, 300, "UNKNOWN", None),
    ])
    breakdown = category_breakdown(tmp_db, "2026-05")
    assert breakdown == {"ネット通販": 1700, "コンビニ": 500, "その他": 300}

def test_highlight_tx_returns_max_since_id(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(1, 1000, "X1", source_id_prefix="a"),
        _tx(2, 5000, "X2", source_id_prefix="b"),
        _tx(3, 800, "X3", source_id_prefix="c"),
    ])
    # We want the max-amount tx with id > 1 (i.e. exclude the first)
    h = highlight_tx(tmp_db, "2026-05", since_max_id=1)
    assert h is not None
    assert h["amount"] == 5000
    assert h["merchant"] == "X2"

def test_highlight_tx_none_when_no_new(tmp_db):
    init_db(tmp_db)
    assert highlight_tx(tmp_db, "2026-05", since_max_id=999) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_analyzer_aggregations.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `card_summary/analyzer.py` (aggregations only — alerts come in Task 9)**

```python
"""Compute monthly aggregations, highlights, and alerts."""
from __future__ import annotations
from datetime import datetime, date
from pathlib import Path
from card_summary.store import open_conn

def month_total(db_path: Path, year_month: str) -> int:
    """Sum of amounts in the given YYYY-MM (inclusive)."""
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ?",
            (year_month,),
        ).fetchone()
    return int(row["s"])

def prev_month_same_day_total(db_path: Path, today: datetime) -> int:
    """Sum of prev-month transactions whose day-of-month <= today.day."""
    if today.month == 1:
        prev_year, prev_month = today.year - 1, 12
    else:
        prev_year, prev_month = today.year, today.month - 1
    prev_ym = f"{prev_year:04d}-{prev_month:02d}"
    upper_day = f"{today.day:02d}"
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ? AND substr(occurred_at, 9, 2) <= ?",
            (prev_ym, upper_day),
        ).fetchone()
    return int(row["s"])

def category_breakdown(db_path: Path, year_month: str) -> dict[str, int]:
    """{category: total_amount}. Null categories are bucketed as 'その他'."""
    with open_conn(db_path) as c:
        rows = c.execute(
            "SELECT COALESCE(category, 'その他') AS cat, SUM(amount) AS s "
            "FROM transactions WHERE substr(occurred_at, 1, 7) = ? GROUP BY cat",
            (year_month,),
        ).fetchall()
    return {r["cat"]: int(r["s"]) for r in rows}

def highlight_tx(db_path: Path, year_month: str, since_max_id: int) -> dict | None:
    """Largest tx in the month with id > since_max_id. None if no new tx."""
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT id, occurred_at, merchant, amount FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ? AND id > ? "
            "ORDER BY amount DESC LIMIT 1",
            (year_month, since_max_id),
        ).fetchone()
    return dict(row) if row else None

def max_tx_id(db_path: Path, year_month: str) -> int:
    """Largest transactions.id in the given month. 0 if none."""
    with open_conn(db_path) as c:
        row = c.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM transactions "
            "WHERE substr(occurred_at, 1, 7) = ?",
            (year_month,),
        ).fetchone()
    return int(row["m"])
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_analyzer_aggregations.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/analyzer.py tests/card_summary/test_analyzer_aggregations.py
git commit -m "新規機能: analyzer 集計関数群 (月累計/前月同日比/カテゴリ/ハイライト)"
```

---

## Task 9: analyzer.py — alerts

**Files:**
- Modify: `card_summary/analyzer.py`
- Create: `tests/card_summary/test_analyzer_alerts.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_analyzer_alerts.py`:
```python
from datetime import datetime
from card_summary.store import init_db, upsert_transactions, Transaction
from card_summary.analyzer import detect_alerts, Alert

def _tx(day: int, amount: int, merchant="X", category=None, month=5, prefix="m"):
    return Transaction(
        occurred_at=datetime(2026, month, day, 12, 0).isoformat(),
        merchant=merchant, amount=amount, category=category,
        source="gmail", source_id=f"{prefix}-{day}-{amount}-{merchant}",
    )

def test_pace_alert_triggers_when_projection_exceeds_threshold(tmp_db):
    init_db(tmp_db)
    # April: ¥30,000 total
    upsert_transactions(tmp_db, [_tx(15, 30000, prefix="apr", month=4)])
    # May 7: ¥10,000 in 7 days → projection = 10000/7*30 = ~42,857
    # threshold: prev_month (30000) * 1.3 = 39,000 → trigger
    upsert_transactions(tmp_db, [_tx(7, 10000, prefix="may")])
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert any(a.kind == "pace" for a in alerts)

def test_pace_alert_does_not_trigger_when_below_threshold(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [_tx(15, 50000, prefix="apr", month=4)])
    upsert_transactions(tmp_db, [_tx(7, 10000, prefix="may")])  # projection ~42,857 < 65,000
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert not any(a.kind == "pace" for a in alerts)

def test_category_alert_triggers_on_2x(tmp_db):
    init_db(tmp_db)
    # prev month same-day food: ¥3,000
    upsert_transactions(tmp_db, [_tx(5, 3000, "A", "食費", month=4, prefix="apr")])
    # this month food: ¥7,000 → 2.33x → trigger
    upsert_transactions(tmp_db, [_tx(5, 7000, "B", "食費", prefix="may")])
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert any(a.kind == "category" and "食費" in a.message for a in alerts)

def test_single_tx_alert_triggers_on_5x_median(tmp_db):
    init_db(tmp_db)
    # 30-day median ~1000, then a 6000 tx → 6x → trigger
    base = [_tx(d, 1000, prefix=f"base{d}") for d in range(1, 8)]
    upsert_transactions(tmp_db, base)
    upsert_transactions(tmp_db, [_tx(7, 6000, "BIG", prefix="big")])
    alerts = detect_alerts(tmp_db, today=datetime(2026, 5, 7))
    assert any(a.kind == "single" for a in alerts)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_analyzer_alerts.py -v`
Expected: FAIL (ImportError on `detect_alerts`, `Alert`)

- [ ] **Step 3: Append to `card_summary/analyzer.py`**

```python
from dataclasses import dataclass
from datetime import timedelta
from card_summary.config import (
    ALERT_PACE_RATIO, ALERT_CATEGORY_RATIO, ALERT_SINGLE_TX_RATIO,
)

@dataclass(frozen=True)
class Alert:
    kind: str       # 'pace' | 'category' | 'single'
    message: str

def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_first = datetime(year + 1, 1, 1)
    else:
        next_first = datetime(year, month + 1, 1)
    return (next_first - datetime(year, month, 1)).days

def _prev_month_total(db_path: Path, today: datetime) -> int:
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    return month_total(db_path, f"{prev_y:04d}-{prev_m:02d}")

def _prev_month_same_day_category(db_path: Path, today: datetime) -> dict[str, int]:
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    prev_ym = f"{prev_y:04d}-{prev_m:02d}"
    upper_day = f"{today.day:02d}"
    with open_conn(db_path) as c:
        rows = c.execute(
            "SELECT COALESCE(category, 'その他') AS cat, SUM(amount) AS s "
            "FROM transactions WHERE substr(occurred_at, 1, 7) = ? AND substr(occurred_at, 9, 2) <= ? "
            "GROUP BY cat",
            (prev_ym, upper_day),
        ).fetchall()
    return {r["cat"]: int(r["s"]) for r in rows}

def _last_30_days_amounts(db_path: Path, today: datetime) -> list[int]:
    cutoff = (today - timedelta(days=30)).isoformat()
    with open_conn(db_path) as c:
        rows = c.execute(
            "SELECT amount FROM transactions WHERE occurred_at >= ?",
            (cutoff,),
        ).fetchall()
    return [int(r["amount"]) for r in rows]

def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2

def detect_alerts(db_path: Path, today: datetime) -> list[Alert]:
    """Return list of triggered alerts."""
    alerts: list[Alert] = []
    ym = f"{today.year:04d}-{today.month:02d}"
    this_total = month_total(db_path, ym)
    prev_total = _prev_month_total(db_path, today)

    # 1. pace alert
    if today.day > 0 and prev_total > 0:
        days_in = _days_in_month(today.year, today.month)
        projection = (this_total / today.day) * days_in
        if projection > prev_total * ALERT_PACE_RATIO:
            alerts.append(Alert(
                "pace",
                f"月ペース予測: ¥{int(projection):,} (前月 ¥{prev_total:,})",
            ))

    # 2. category alert
    this_cat = category_breakdown(db_path, ym)
    prev_cat = _prev_month_same_day_category(db_path, today)
    for cat, this_amt in this_cat.items():
        prev_amt = prev_cat.get(cat, 0)
        if prev_amt > 0 and this_amt > prev_amt * ALERT_CATEGORY_RATIO:
            ratio = this_amt / prev_amt * 100
            alerts.append(Alert(
                "category",
                f"{cat} が前月同日比 +{int(ratio - 100)}% (今月ペース注意!)",
            ))

    # 3. single tx alert (largest tx today exceeds 5x of 30-day median)
    recent = _last_30_days_amounts(db_path, today)
    med = _median([a for a in recent if a > 0])
    if med > 0:
        with open_conn(db_path) as c:
            row = c.execute(
                "SELECT merchant, amount FROM transactions "
                "WHERE substr(occurred_at, 1, 10) = ? "
                "ORDER BY amount DESC LIMIT 1",
                (today.date().isoformat(),),
            ).fetchone()
        if row and int(row["amount"]) > med * ALERT_SINGLE_TX_RATIO:
            alerts.append(Alert(
                "single",
                f"⚡ 異常高額: {row['merchant']} ¥{int(row['amount']):,} (普段の{int(row['amount']/med)}倍)",
            ))

    return alerts
```

- [ ] **Step 4: Run all analyzer tests**

Run: `.venv/bin/pytest tests/card_summary/test_analyzer_alerts.py tests/card_summary/test_analyzer_aggregations.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add card_summary/analyzer.py tests/card_summary/test_analyzer_alerts.py
git commit -m "新規機能: analyzer に異常検知 (ペース/カテゴリ/単発) を追加"
```

---

## Task 10: analyzer SummaryReport + has_changed

**Files:**
- Modify: `card_summary/analyzer.py`
- Create: `tests/card_summary/test_analyzer_report.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_analyzer_report.py`:
```python
from datetime import datetime
from card_summary.store import init_db, upsert_transactions, Transaction, set_summary_state
from card_summary.analyzer import compute_report, has_changed

def _tx(day, amount, merchant="X", category="その他", month=5, prefix="m"):
    return Transaction(
        occurred_at=datetime(2026, month, day, 12, 0).isoformat(),
        merchant=merchant, amount=amount, category=category,
        source="gmail", source_id=f"{prefix}-{day}-{amount}-{merchant}",
    )

def test_compute_report_assembles_all_fields(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [
        _tx(7, 1000, "AMAZON", "ネット通販"),
        _tx(7, 200, "SEVEN", "コンビニ"),
    ])
    r = compute_report(tmp_db, today=datetime(2026, 5, 7), since_max_id=0)
    assert r.month_total == 1200
    assert r.category_breakdown["ネット通販"] == 1000
    assert r.highlight is not None
    assert r.max_tx_id > 0
    assert isinstance(r.alerts, list)
    assert isinstance(r.breakdown_hash, str) and len(r.breakdown_hash) == 64
    assert isinstance(r.alert_hash, str) and len(r.alert_hash) == 64

def test_has_changed_first_time_is_changed(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [_tx(7, 1000)])
    r = compute_report(tmp_db, today=datetime(2026, 5, 7), since_max_id=0)
    assert has_changed(prev=None, report=r) is True

def test_has_changed_returns_false_when_all_four_match(tmp_db):
    init_db(tmp_db)
    upsert_transactions(tmp_db, [_tx(7, 1000)])
    r = compute_report(tmp_db, today=datetime(2026, 5, 7), since_max_id=0)
    set_summary_state(
        tmp_db, "morning",
        last_posted_at="2026-05-07T07:00:00",
        last_total=r.month_total,
        last_breakdown_hash=r.breakdown_hash,
        last_max_tx_id=r.max_tx_id,
        last_alert_hash=r.alert_hash,
        last_thread_id="abc",
    )
    from card_summary.store import get_summary_state
    prev = get_summary_state(tmp_db, "morning")
    assert has_changed(prev=prev, report=r) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_analyzer_report.py -v`
Expected: FAIL (ImportError on `compute_report`, `has_changed`)

- [ ] **Step 3: Append to `card_summary/analyzer.py`**

```python
import hashlib
import json

@dataclass(frozen=True)
class SummaryReport:
    today: datetime
    year_month: str
    month_total: int
    prev_month_same_day: int
    category_breakdown: dict[str, int]
    highlight: dict | None
    alerts: list[Alert]
    max_tx_id: int
    breakdown_hash: str
    alert_hash: str

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def compute_report(db_path: Path, today: datetime, since_max_id: int) -> SummaryReport:
    ym = f"{today.year:04d}-{today.month:02d}"
    breakdown = category_breakdown(db_path, ym)
    alerts = detect_alerts(db_path, today)
    breakdown_json = json.dumps(breakdown, sort_keys=True, ensure_ascii=False)
    alerts_json = json.dumps([(a.kind, a.message) for a in alerts], ensure_ascii=False)
    return SummaryReport(
        today=today,
        year_month=ym,
        month_total=month_total(db_path, ym),
        prev_month_same_day=prev_month_same_day_total(db_path, today),
        category_breakdown=breakdown,
        highlight=highlight_tx(db_path, ym, since_max_id),
        alerts=alerts,
        max_tx_id=max_tx_id(db_path, ym),
        breakdown_hash=_sha256(breakdown_json),
        alert_hash=_sha256(alerts_json),
    )

def has_changed(prev: dict | None, report: SummaryReport) -> bool:
    """Returns True if any of the 4 tracked values differ from prev."""
    if prev is None:
        return True
    return (
        prev["last_total"] != report.month_total
        or prev["last_breakdown_hash"] != report.breakdown_hash
        or prev["last_max_tx_id"] != report.max_tx_id
        or prev["last_alert_hash"] != report.alert_hash
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_analyzer_report.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/analyzer.py tests/card_summary/test_analyzer_report.py
git commit -m "新規機能: SummaryReport と has_changed (4値ハッシュ判定) を追加"
```

---

## Task 11: formatter.py

**Files:**
- Create: `card_summary/formatter.py`
- Create: `tests/card_summary/test_formatter.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_formatter.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_formatter.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `card_summary/formatter.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_formatter.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/formatter.py tests/card_summary/test_formatter.py
git commit -m "新規機能: formatter (Discord メッセージ生成)"
```

---

## Task 12: gmail_fetcher.py — OAuth helper

**Files:**
- Create: `card_summary/gmail_fetcher.py`
- Create: `tests/card_summary/test_gmail_fetcher_auth.py`

- [ ] **Step 1: Write `card_summary/gmail_fetcher.py` (auth only — fetch comes Task 13)**

```python
"""Gmail API client. Direct REST, not MCP, since MCP is Claude-Code-only."""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from card_summary.config import GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH, GMAIL_SCOPES

log = logging.getLogger(__name__)

class GmailAuthError(RuntimeError):
    pass

def authenticate(
    credentials_path: Path = GMAIL_CREDENTIALS_PATH,
    token_path: Path = GMAIL_TOKEN_PATH,
) -> Credentials:
    """Load saved creds or run OAuth flow. Token is refreshed if expired."""
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise GmailAuthError(
                    f"Missing OAuth client credentials at {credentials_path}. "
                    f"Download from Google Cloud Console > OAuth Client > Desktop type."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return creds

def build_service(creds: Credentials) -> Any:
    """Return a googleapiclient gmail.users service."""
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
```

- [ ] **Step 2: Write minimal test (no actual OAuth flow)**

`tests/card_summary/test_gmail_fetcher_auth.py`:
```python
from pathlib import Path
import pytest
from card_summary.gmail_fetcher import authenticate, GmailAuthError

def test_authenticate_raises_when_no_creds(tmp_path):
    fake_creds = tmp_path / "no.json"
    fake_token = tmp_path / "no_token.json"
    with pytest.raises(GmailAuthError):
        authenticate(credentials_path=fake_creds, token_path=fake_token)
```

- [ ] **Step 3: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_gmail_fetcher_auth.py -v`
Expected: PASS (1 test). Other auth paths require real OAuth interaction and are exercised in Task 18 (manual deployment).

- [ ] **Step 4: Commit**

```bash
git add card_summary/gmail_fetcher.py tests/card_summary/test_gmail_fetcher_auth.py
git commit -m "新規機能: gmail_fetcher の OAuth ヘルパーと service builder"
```

---

## Task 13: gmail_fetcher.py — fetch_new_since

**Files:**
- Modify: `card_summary/gmail_fetcher.py`
- Create: `tests/card_summary/test_gmail_fetcher_fetch.py`

- [ ] **Step 1: Write failing test (mocked Gmail service)**

`tests/card_summary/test_gmail_fetcher_fetch.py`:
```python
from unittest.mock import MagicMock
from card_summary.gmail_fetcher import fetch_new_since

def _make_service(messages: list[dict]) -> MagicMock:
    """Build a mock that mimics service.users().messages().list().execute() etc."""
    svc = MagicMock()
    list_mock = svc.users().messages().list
    list_mock.return_value.execute.return_value = {
        "messages": [{"id": m["id"]} for m in messages]
    }
    def get_side_effect(userId: str, id: str, format: str):
        match = next(m for m in messages if m["id"] == id)
        get_call = MagicMock()
        get_call.execute.return_value = match
        return get_call
    svc.users().messages().get.side_effect = get_side_effect
    return svc

def test_fetch_new_since_returns_id_and_body():
    msgs = [
        {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": "44GT44KT44Gr44Gh44Gv"},  # base64url('こんにちは')
            },
        },
    ]
    svc = _make_service(msgs)
    results = list(fetch_new_since(svc, query="from:eposcard@eposcard.co.jp", since="2026-05-01"))
    assert len(results) == 1
    msg_id, body = results[0]
    assert msg_id == "msg-1"
    assert "こんにちは" in body

def test_fetch_new_since_handles_empty():
    svc = MagicMock()
    svc.users().messages().list.return_value.execute.return_value = {}
    assert list(fetch_new_since(svc, query="x", since="2026-05-01")) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_gmail_fetcher_fetch.py -v`
Expected: FAIL (ImportError on `fetch_new_since`)

- [ ] **Step 3: Append to `card_summary/gmail_fetcher.py`**

```python
import base64
from datetime import datetime
from typing import Iterator

def _decode_body(payload: dict) -> str:
    """Recursively walk the payload tree to extract text/plain (or text/html as fallback)."""
    mime = payload.get("mimeType", "")
    if mime.startswith("text/"):
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    return ""

def _to_gmail_after(iso_str: str) -> str:
    """Gmail search query takes after:YYYY/MM/DD form."""
    dt = datetime.fromisoformat(iso_str)
    return dt.strftime("%Y/%m/%d")

def fetch_new_since(service, query: str, since: str | None) -> Iterator[tuple[str, str]]:
    """Yield (message_id, body_text) for matching mails newer than `since` (ISO8601).

    `service` is a googleapiclient gmail.users service (or a Mock with the same shape).
    """
    full_query = query
    if since:
        full_query = f"{query} after:{_to_gmail_after(since)}"
    resp = service.users().messages().list(userId="me", q=full_query, maxResults=100).execute()
    msgs = resp.get("messages") or []
    for m in msgs:
        msg_id = m["id"]
        full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        body = _decode_body(full.get("payload") or {})
        yield (msg_id, body)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_gmail_fetcher_fetch.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/gmail_fetcher.py tests/card_summary/test_gmail_fetcher_fetch.py
git commit -m "新規機能: gmail_fetcher.fetch_new_since (Gmail API 直叩き + base64decode)"
```

---

## Task 14: scheduler.run_slot — end-to-end (mocked) flow

**Files:**
- Create: `card_summary/scheduler.py`
- Create: `tests/card_summary/test_scheduler_run_slot.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_scheduler_run_slot.py`:
```python
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
```

- [ ] **Step 2: Install pytest-asyncio if missing**

```bash
.venv/bin/pip install pytest-asyncio>=0.23
```

Add to `requirements.txt`:
```
pytest-asyncio>=0.23
```

Add `pytest.ini` at repo root (only this — no conftest changes needed):
```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_scheduler_run_slot.py -v`
Expected: FAIL (ModuleNotFoundError: card_summary.scheduler)

- [ ] **Step 4: Write `card_summary/scheduler.py`**

```python
"""Scheduler: orchestrates one slot's batch and the daily timer loop."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime
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
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_scheduler_run_slot.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add card_summary/scheduler.py tests/card_summary/test_scheduler_run_slot.py requirements.txt pytest.ini
git commit -m "新規機能: scheduler.run_slot 統合 (取得→パース→分類→保存→投稿→sessions登録)"
```

---

## Task 15: scheduler.start_scheduler — daily timer

**Files:**
- Modify: `card_summary/scheduler.py`
- Create: `tests/card_summary/test_scheduler_timer.py`

- [ ] **Step 1: Write failing test**

`tests/card_summary/test_scheduler_timer.py`:
```python
import asyncio
from unittest.mock import AsyncMock
import pytest
from freezegun import freeze_time
from card_summary.scheduler import _next_slot_to_run, _seconds_until

def test_next_slot_morning_after_midnight():
    with freeze_time("2026-05-07 03:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "morning"
    assert dt.hour == 7

def test_next_slot_afternoon_after_morning():
    with freeze_time("2026-05-07 08:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "afternoon"
    assert dt.hour == 15

def test_next_slot_night_after_afternoon():
    with freeze_time("2026-05-07 16:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "night"
    assert dt.hour == 22

def test_next_slot_wraps_to_next_day_morning():
    with freeze_time("2026-05-07 23:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "morning"
    assert dt.day == 8

def test_seconds_until_positive():
    with freeze_time("2026-05-07 03:00:00"):
        slot, dt = _next_slot_to_run()
        sec = _seconds_until(dt)
    assert sec == 4 * 3600
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/card_summary/test_scheduler_timer.py -v`
Expected: FAIL (ImportError on `_next_slot_to_run`, `_seconds_until`)

- [ ] **Step 3: Append to `card_summary/scheduler.py`**

```python
from datetime import timedelta

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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/pytest tests/card_summary/test_scheduler_timer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add card_summary/scheduler.py tests/card_summary/test_scheduler_timer.py
git commit -m "新規機能: scheduler に start_scheduler (asyncio 時刻ループ) を追加"
```

---

## Task 16: bot.py — scheduler startup + context-file injection

**Files:**
- Modify: `bot.py`
- Modify: `card_summary/scheduler.py` (add Discord-side helpers)

> **Critical context:** Existing `bot.py` line 858-890 already routes thread messages in the kanojo forum to `handle_thread_message` — see `is_our_channel = CONTROL_CHANNEL_ID and parent_id == CONTROL_CHANNEL_ID`. So mention緩和 is unnecessary. We only need (a) start the scheduler in `on_ready` for the kanojo bot, and (b) inject the context file in `handle_thread_message` when the session is brand-new.

- [ ] **Step 1: Add Discord glue to `card_summary/scheduler.py`**

Append:
```python
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
```

- [ ] **Step 2: Modify `bot.py` — add scheduler startup in `on_ready`**

Replace the `on_ready` handler at line 757-764 with:
```python
@client.event
async def on_ready():
    print(f"[{BOT_NAME}] Logged in as {client.user}")
    print(f"[{BOT_NAME}] Project: {PROJECT_DISPLAY} -> {PROJECT_DIR}")
    print(f"[{BOT_NAME}] Control channel: {CONTROL_CHANNEL_ID or 'any (mention or DM)'}")
    print(f"[{BOT_NAME}] Auto-pull: {AUTO_PULL} | Worktree: {WORKTREE_ENABLED}")

    if BOT_NAME == "kanojo":
        from card_summary.scheduler import (
            start_scheduler, post_to_kanojo_forum, register_kanojo_session,
        )
        from card_summary.config import KANOJO_FORUM_CHANNEL_ID

        async def _fetch(since):
            from card_summary.gmail_fetcher import authenticate, build_service, fetch_new_since
            from card_summary.config import GMAIL_QUERY
            creds = authenticate()
            svc = build_service(creds)
            return list(fetch_new_since(svc, GMAIL_QUERY, since))

        def _llm(merchant: str) -> str:
            # Initial implementation: bucket every unknown merchant as 'その他'.
            # The categorizer caches the result so each unknown merchant only hits this once.
            # Replace this stub with an Anthropic Haiku call when budget allows; the contract
            # is `(merchant: str) -> category in CATEGORIES`. See spec §8 for the prompt.
            return "その他"

        async def _post(thread_name, body):
            return await post_to_kanojo_forum(client, KANOJO_FORUM_CHANNEL_ID, thread_name, body)

        async def _register(thread, slot, summary_text):
            await register_kanojo_session(
                SESSIONS_FILE, thread, slot, summary_text, PROJECT_DIR
            )

        asyncio.create_task(start_scheduler(
            fetch_new=_fetch, llm_fn=_llm,
            post_to_forum=_post, register_session=_register,
        ))
        print(f"[{BOT_NAME}] card_summary scheduler started")
```

- [ ] **Step 3: Modify `bot.py:handle_thread_message` — inject context-file on first call**

Find the function at line 680 and replace the `prompt = build_prompt_with_inbox(...)` line with:
```python
    saved_inbox = await save_inbox_attachments(message, work_dir)
    user_text = message.content.strip()

    # Kanojo bot: inject summary context on the first call of a kanojo-posted thread
    ctx_file = session.get("kanojo_context_file")
    if BOT_NAME == "kanojo" and session.get("sessionId") is None and ctx_file:
        try:
            ctx_text = Path(ctx_file).read_text(encoding="utf-8")
            user_text = (
                "<background>このスレッドは以下のサマリーを Bot が投稿して始まりました。"
                "ユーザーの質問はこのサマリーに関するものとして回答してください。\n\n"
                f"{ctx_text}\n</background>\n\n"
                f"ユーザーの質問: {user_text}"
            )
        except Exception as e:
            print(f"[kanojo] failed to read context file {ctx_file}: {e}", file=sys.stderr)

    prompt = build_prompt_with_inbox(user_text, saved_inbox)
```

- [ ] **Step 4: Smoke-test bot.py imports**

```bash
cd ~/discord-bots
.venv/bin/python -c "import bot" 2>&1 | head -10
```
Expected: prints "Usage: python bot.py <bot_name>" then exits with code 1 (because no argv). No ImportError. (The `bot.py` module exits at import time when argv is missing — invoke as a module check via `-c "import importlib; importlib.import_module('bot')"` is the same thing; the SystemExit from `sys.exit(1)` is expected.)

If the import fails for any other reason (`ImportError`, `NameError`), fix the offending line.

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest tests/card_summary/ -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add bot.py card_summary/scheduler.py
git commit -m "新規機能: kanojo bot に scheduler 起動 と context-file 注入を追加"
```

---

## Task 17: config.json — register kanojo bot + plist regen

**Files:**
- Modify: `config.json`
- Modify: `launchd/generate-plists.sh` (run only)

- [ ] **Step 1: Add kanojo entry to `config.json`**

Edit `~/discord-bots/config.json` and add a new entry under `bots`:
```json
"kanojo": {
  "name": "kanojo",
  "token_keychain_account": "kanojo-bot-token",
  "dir": "~/discord-bots",
  "emoji": "💳",
  "control_channel_id": null
}
```

The `control_channel_id` will be filled in Task 18 after the forum is created. Setting it to `null` for now is intentional — it means the bot only responds to mentions until configured.

- [ ] **Step 2: Regenerate plists**

```bash
cd ~/discord-bots
bash launchd/generate-plists.sh
ls launchd/*.plist
```
Expected: `com.akimare.bot-kanojo.plist` is now present alongside the others.

- [ ] **Step 3: Commit**

```bash
git add config.json launchd/com.akimare.bot-kanojo.plist
git commit -m "設定: config.json に kanojo bot エントリを追加 + plist 再生成"
```

---

## Task 18: Manual deployment — token, OAuth, forum, channel ID

> **This task is a runbook, not code. Each step is a manual action; mark complete when done.**

- [ ] **Step 1: Save kanojo bot token to keychain**

If you have the token from Discord Developer Portal:
```bash
security add-generic-password -a "kanojo-bot-token" -s "discord-bot" -w "<token-here>" -U
# verify
security find-generic-password -a "kanojo-bot-token" -s "discord-bot" -w
```

If you don't yet have the token, go to Discord Developer Portal → applications → kanojo → Bot → Reset Token → copy the new token, then run the command above. Note: resetting invalidates the old token; if the bot is already deployed elsewhere, coordinate.

- [ ] **Step 2: Set up Gmail OAuth client**

1. Go to https://console.cloud.google.com/ → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID → Application type: Desktop
3. Download JSON → save as `~/discord-bots/data/gmail_credentials.json`
4. Run the OAuth flow once locally (will open browser):
   ```bash
   cd ~/discord-bots
   .venv/bin/python -c "from card_summary.gmail_fetcher import authenticate; authenticate()"
   ```
   This writes `data/gmail_token.json`. Verify that `from:eposcard@eposcard.co.jp` returns mails:
   ```bash
   .venv/bin/python -c "
   from card_summary.gmail_fetcher import authenticate, build_service, fetch_new_since
   creds = authenticate(); svc = build_service(creds)
   for msg_id, body in list(fetch_new_since(svc, 'from:eposcard@eposcard.co.jp', '2026-04-01'))[:3]:
       print(msg_id, body[:100])
   "
   ```

- [ ] **Step 3: Create kanojo forum on Discord**

1. In the target Discord server, create a Forum channel named `💳 kanojo` (or your preferred name)
2. Invite the kanojo bot to the server (Discord Developer Portal → OAuth2 → URL Generator → scopes: `bot` + `applications.commands`, permissions: View Channels, Send Messages, Create Public Threads, Manage Threads)
3. Right-click the forum channel → Copy Channel ID
4. Edit `~/discord-bots/config.json`: set `"kanojo": {"control_channel_id": <forum_channel_id>}`
5. Edit `card_summary/config.py`: set `KANOJO_FORUM_CHANNEL_ID = <same_id>`

- [ ] **Step 4: Commit channel ID changes**

```bash
git add config.json card_summary/config.py
git commit -m "設定: kanojo フォーラム channel_id を設定"
```

- [ ] **Step 5: Deploy to macmini**

```bash
ssh macmini "cd ~/discord-bots && git pull && .venv/bin/pip install -r requirements.txt && bash launchd/install-macmini.sh"
```

The install script (assumed pattern from existing 4 bots) will (re)load the plist for kanojo. Check status:
```bash
ssh macmini "launchctl list | grep kanojo"
ssh macmini "tail -20 ~/discord-bots/logs/kanojo.log"
```
Expected: bot is logged in, scheduler started message present.

- [ ] **Step 6: Copy gmail_credentials/token to macmini**

```bash
scp ~/discord-bots/data/gmail_credentials.json macmini:~/discord-bots/data/
scp ~/discord-bots/data/gmail_token.json macmini:~/discord-bots/data/
```

(Alternative: re-run the OAuth flow on macmini directly if it has display.)

---

## Task 19: Smoke test — manual one-slot run

**Files:**
- (none — manual verification)

- [ ] **Step 1: Trigger morning slot manually on macmini**

```bash
ssh macmini "cd ~/discord-bots && .venv/bin/python -c \"
import asyncio, json
from pathlib import Path
from card_summary.scheduler import run_slot
from card_summary.gmail_fetcher import authenticate, build_service, fetch_new_since
from card_summary.config import GMAIL_QUERY

async def fetch(since):
    creds = authenticate(); svc = build_service(creds)
    return list(fetch_new_since(svc, GMAIL_QUERY, since))

async def post(thread_name, body):
    print('[would post]', thread_name); print(body); print()
    class FakeThread: id = 0; name = thread_name
    return FakeThread()

async def register(*args, **kwargs): pass

asyncio.run(run_slot(slot='morning', fetch_new=fetch, llm_fn=lambda m: 'その他',
                     post_to_forum=post, register_session=register))
\""
```
Expected output: a formatted summary printed to stdout (no Discord post yet — `post` is a stub).

- [ ] **Step 2: Trigger via real Discord post**

Restart the kanojo bot so the scheduler picks up any config changes:
```bash
ssh macmini "launchctl kickstart -k gui/$(id -u $USER)/com.akimare.bot-kanojo"
```

Then wait for the next scheduled slot (7:00 / 15:00 / 22:00 JST). The scheduler logs `next slot=... in Ns` on startup — confirm with:
```bash
ssh macmini "tail -f ~/discord-bots/logs/kanojo.log"
```

Verify after the slot fires:
1. A thread appears in the kanojo forum on Discord with the summary text.
2. `~/discord-bots/sessions-kanojo.json` on macmini has a new entry with `kanojo_context_file` populated.
3. `~/discord-bots/data/card_summary/contexts/<thread_id>.txt` exists and matches the posted summary.

- [ ] **Step 3: Test reply flow**

Reply to the auto-posted thread with: `Amazon の3,200円って何だっけ？`

Expected:
- bot.py picks up the message via `is_thread + is_our_channel` path
- `handle_thread_message` runs Claude with the context-file-injected prompt
- Reply quotes the summary content back

- [ ] **Step 4: Test silent mode**

Manually trigger run_slot again with no new mails (e.g., re-run Step 1). Expected: no Discord post, log line `[morning] no change — silent`.

- [ ] **Step 5: Commit any documentation updates**

```bash
cd ~/discord-bots
git add docs/superpowers/plans/2026-05-07-card-summary-bot-plan.md
git commit -m "ドキュメント: card-summary-bot 動作確認結果を反映"
```

---

## Task 20: Reconciler (optional, monthly browser-use)

**Files:**
- Create: `card_summary/reconciler.py`
- Create: `tests/card_summary/test_reconciler.py`

> Optional. Skip until at least 1 month of Gmail-based summaries have run successfully. Add only when you want monthly confirmed-amount cross-checks.

- [ ] **Step 1: Write `card_summary/reconciler.py` skeleton**

```python
"""Monthly Epos Net reconciliation via browser-use."""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path
from card_summary.store import upsert_monthly_close, upsert_transactions, Transaction

log = logging.getLogger(__name__)

class ReconcileResult:
    def __init__(self, year_month: str, confirmed_amount: int, gmail_total: int):
        self.year_month = year_month
        self.confirmed_amount = confirmed_amount
        self.gmail_total = gmail_total
        self.diff = confirmed_amount - gmail_total

async def reconcile_via_browser_use(
    db_path: Path,
    year_month: str,
    *,
    browser_run: callable,  # async fn returning {'confirmed_amount': int, 'transactions': [...] }
) -> ReconcileResult:
    """Drive a browser-use task to log into Epos Net, fetch the prior month's
    confirmed total + line items, and reconcile against our DB.
    """
    from card_summary.analyzer import month_total
    data = await browser_run(year_month)
    confirmed = int(data["confirmed_amount"])
    upsert_monthly_close(db_path, year_month, confirmed, datetime.now().isoformat())
    extras = [Transaction(**t, source="epos_net") for t in data.get("transactions", [])]
    upsert_transactions(db_path, extras)
    gmail_total = month_total(db_path, year_month)
    return ReconcileResult(year_month, confirmed, gmail_total)
```

- [ ] **Step 2: Test with mock browser-use**

`tests/card_summary/test_reconciler.py`:
```python
from unittest.mock import AsyncMock
import pytest
from card_summary.store import init_db, get_monthly_close
from card_summary.reconciler import reconcile_via_browser_use

@pytest.mark.asyncio
async def test_reconcile_writes_monthly_close(tmp_db):
    init_db(tmp_db)
    browser_run = AsyncMock(return_value={
        "confirmed_amount": 58000,
        "transactions": [],
    })
    result = await reconcile_via_browser_use(tmp_db, "2026-04", browser_run=browser_run)
    assert result.confirmed_amount == 58000
    assert get_monthly_close(tmp_db, "2026-04") == 58000
```

- [ ] **Step 3: Run + commit**

```bash
.venv/bin/pytest tests/card_summary/test_reconciler.py -v
git add card_summary/reconciler.py tests/card_summary/test_reconciler.py
git commit -m "新規機能: 月次 reconciler スケルトン (browser-use 連携)"
```

> The actual browser-use script is left for later — write it when you have a stable kanojo flow running for at least one full billing cycle. Hook it into `start_scheduler` once ready.

---

## Implementation Order Summary

| # | Task | TDD? | Depends on |
|---|---|---|---|
| 1 | Package skeleton | — | — |
| 2 | SQLite schema | ✅ | 1 |
| 3 | Transaction upsert | ✅ | 2 |
| 4 | State tables | ✅ | 2 |
| 5 | parser normal | ✅ | 3 |
| 6 | parser cancel/overseas | ✅ | 5 |
| 7 | categorizer | ✅ | 4 |
| 8 | analyzer aggregations | ✅ | 3, 4 |
| 9 | analyzer alerts | ✅ | 8 |
| 10 | analyzer report + has_changed | ✅ | 9 |
| 11 | formatter | ✅ | 10 |
| 12 | gmail_fetcher auth | ✅ | 1 |
| 13 | gmail_fetcher fetch | ✅ | 12 |
| 14 | scheduler.run_slot | ✅ | 5,7,10,11,13 |
| 15 | scheduler.start_scheduler | ✅ | 14 |
| 16 | bot.py wiring | partial | 15 |
| 17 | config.json + plist | manual | 16 |
| 18 | Manual deployment | manual | 17 |
| 19 | Smoke test | manual | 18 |
| 20 | Reconciler (optional) | ✅ | 19 |

Total estimated time: ~6-8 hours of focused work for Tasks 1-19. Task 20 is +1-2 hours when ready.
