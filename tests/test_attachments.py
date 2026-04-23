"""Tests for attachment helpers (inbox prompt, filter, chunk)."""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from attachments import (
    format_inbox_for_prompt,
    filter_sendable,
    chunk_for_messages,
    MAX_FILE_BYTES,
    MAX_FILES_PER_MESSAGE,
)


# ---------------------------------------------------------------------------
# format_inbox_for_prompt
# ---------------------------------------------------------------------------

def test_inbox_prompt_empty():
    assert format_inbox_for_prompt([]) == ""


def test_inbox_prompt_single(tmp_path):
    f = tmp_path / "photo.png"
    f.write_bytes(b"x")
    out = format_inbox_for_prompt([f])
    assert "photo.png" in out
    assert str(f) in out
    # Should mention the inbox convention so Claude knows where to read
    assert "_inbox" in out or "添付" in out


def test_inbox_prompt_multiple(tmp_path):
    files = []
    for name in ("a.png", "b.pdf"):
        p = tmp_path / name
        p.write_bytes(b"x")
        files.append(p)
    out = format_inbox_for_prompt(files)
    assert "a.png" in out
    assert "b.pdf" in out


# ---------------------------------------------------------------------------
# filter_sendable — split into OK and TOO-LARGE lists
# ---------------------------------------------------------------------------

def test_filter_sendable_all_ok(tmp_path):
    f = tmp_path / "small.pdf"
    f.write_bytes(b"x" * 100)
    ok, rejected = filter_sendable([f])
    assert ok == [f]
    assert rejected == []


def test_filter_sendable_rejects_oversize(tmp_path):
    big = tmp_path / "huge.pdf"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    small = tmp_path / "tiny.pdf"
    small.write_bytes(b"x")
    ok, rejected = filter_sendable([big, small])
    assert ok == [small]
    assert len(rejected) == 1
    assert rejected[0][0] == big
    # Reason should include the size for debuggability
    assert "MB" in rejected[0][1] or "byte" in rejected[0][1].lower()


# ---------------------------------------------------------------------------
# chunk_for_messages — group into ≤10-file batches
# ---------------------------------------------------------------------------

def test_chunk_empty():
    assert chunk_for_messages([]) == []


def test_chunk_fits_single_message(tmp_path):
    files = [tmp_path / f"{i}.txt" for i in range(5)]
    chunks = chunk_for_messages(files)
    assert chunks == [files]


def test_chunk_splits_over_limit(tmp_path):
    files = [tmp_path / f"{i}.txt" for i in range(MAX_FILES_PER_MESSAGE + 3)]
    chunks = chunk_for_messages(files)
    assert len(chunks) == 2
    assert len(chunks[0]) == MAX_FILES_PER_MESSAGE
    assert len(chunks[1]) == 3
