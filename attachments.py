"""Pure helpers for Discord attachment IO: prompt formatting, size filtering, batching."""
from pathlib import Path

# Discord upload limit for non-Nitro bot accounts (2026: 10 MiB).
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10


def format_inbox_for_prompt(files: list[Path]) -> str:
    """Return a prompt addendum listing inbox files, or '' when none."""
    if not files:
        return ""
    lines = ["[_inbox に添付ファイルあり]"]
    for f in files:
        lines.append(f"- {f.name} (path: {f})")
    return "\n".join(lines)


def filter_sendable(
    files: list[Path], max_bytes: int = MAX_FILE_BYTES
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Split files into (sendable, rejected). Each rejected entry is (path, reason)."""
    ok: list[Path] = []
    rejected: list[tuple[Path, str]] = []
    for f in files:
        size = f.stat().st_size
        if size <= max_bytes:
            ok.append(f)
        else:
            mb = size / (1024 * 1024)
            rejected.append((f, f"{mb:.1f} MB > {max_bytes // (1024*1024)} MB limit"))
    return ok, rejected


def chunk_for_messages(
    files: list[Path], per_message: int = MAX_FILES_PER_MESSAGE
) -> list[list[Path]]:
    """Group files into ≤ per_message batches for separate Discord messages."""
    return [files[i : i + per_message] for i in range(0, len(files), per_message)]
