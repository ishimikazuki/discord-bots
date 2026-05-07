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
