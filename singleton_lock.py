"""Per-bot singleton lock backed by fcntl.flock.

Multiple bot.py instances for the same BOT_NAME would each connect to
Discord with the same token and process every gateway message — leading to
duplicate replies. flock is released automatically when the process
exits, so stale lock files never block a restart.
"""

from __future__ import annotations

import errno
import fcntl
import os
import sys
from pathlib import Path


class SingletonLockError(RuntimeError):
    def __init__(self, bot_name: str, holder_pid: str):
        self.bot_name = bot_name
        self.holder_pid = holder_pid
        super().__init__(
            f"another bot.py [{bot_name}] instance is running (pid={holder_pid})"
        )


def lock_path(base_dir: Path, bot_name: str) -> Path:
    return base_dir / f".lock-{bot_name}.pid"


def acquire(base_dir: Path, bot_name: str) -> int:
    """Acquire an exclusive lock for `bot_name` under `base_dir`.

    Returns the file descriptor (kept open for the process lifetime).
    Raises SingletonLockError if another live process holds the lock.
    """
    path = lock_path(base_dir, bot_name)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
            os.close(fd)
            raise
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            existing = os.read(fd, 64).decode(errors="replace").strip() or "?"
        except OSError:
            existing = "?"
        os.close(fd)
        raise SingletonLockError(bot_name, existing) from None

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def acquire_or_exit(base_dir: Path, bot_name: str) -> int:
    try:
        return acquire(base_dir, bot_name)
    except SingletonLockError as e:
        print(f"[FATAL] {e}. refusing to start.", file=sys.stderr)
        sys.exit(1)
