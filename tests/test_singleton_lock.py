"""Tests for per-bot singleton lock."""

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from singleton_lock import (
    SingletonLockError,
    acquire,
    lock_path,
)


def test_lock_path_is_per_bot(tmp_path: Path):
    a = lock_path(tmp_path, "alpha")
    b = lock_path(tmp_path, "beta")
    assert a != b
    assert a.name == ".lock-alpha.pid"
    assert b.name == ".lock-beta.pid"


def test_acquire_writes_pid_to_file(tmp_path: Path):
    fd = acquire(tmp_path, "alpha")
    try:
        path = lock_path(tmp_path, "alpha")
        assert path.exists()
        assert path.read_text().strip() == str(os.getpid())
    finally:
        os.close(fd)


def test_second_process_in_same_process_raises(tmp_path: Path):
    """Two acquire() calls in the same process for different bots both succeed,
    but acquiring the same bot twice fails because the first fd still holds the lock."""
    fd_alpha = acquire(tmp_path, "alpha")
    fd_beta = acquire(tmp_path, "beta")
    try:
        with pytest.raises(SingletonLockError) as exc:
            acquire(tmp_path, "alpha")
        assert exc.value.bot_name == "alpha"
        assert exc.value.holder_pid == str(os.getpid())
    finally:
        os.close(fd_alpha)
        os.close(fd_beta)


def _hold_lock(tmp_path_str: str, bot: str, ready_path: str, release_path: str) -> None:
    """Helper for the cross-process test: acquire and wait until told to release."""
    from singleton_lock import acquire as _acquire

    fd = _acquire(Path(tmp_path_str), bot)
    Path(ready_path).touch()
    while not Path(release_path).exists():
        time.sleep(0.05)
    os.close(fd)


def test_second_os_process_is_rejected(tmp_path: Path):
    """A real second process trying the same bot lock must fail with SingletonLockError."""
    ready = tmp_path / "ready.flag"
    release = tmp_path / "release.flag"

    ctx = multiprocessing.get_context("spawn")
    holder = ctx.Process(
        target=_hold_lock,
        args=(str(tmp_path), "alpha", str(ready), str(release)),
    )
    holder.start()
    try:
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "holder did not acquire lock in time"

        with pytest.raises(SingletonLockError) as exc:
            acquire(tmp_path, "alpha")
        assert exc.value.bot_name == "alpha"
        assert exc.value.holder_pid == str(holder.pid)
    finally:
        release.touch()
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=2)


def test_lock_released_when_holder_dies(tmp_path: Path):
    """When the holding process exits, the next acquire must succeed (flock auto-releases)."""
    ready = tmp_path / "ready.flag"
    release = tmp_path / "release.flag"

    ctx = multiprocessing.get_context("spawn")
    holder = ctx.Process(
        target=_hold_lock,
        args=(str(tmp_path), "alpha", str(ready), str(release)),
    )
    holder.start()

    deadline = time.time() + 5
    while not ready.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert ready.exists()

    release.touch()
    holder.join(timeout=5)
    assert holder.exitcode == 0

    fd = acquire(tmp_path, "alpha")
    try:
        assert lock_path(tmp_path, "alpha").read_text().strip() == str(os.getpid())
    finally:
        os.close(fd)
