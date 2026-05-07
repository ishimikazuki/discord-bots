"""Opt-in helpers for bot-authored Discord health-check messages."""


def parse_id_set(raw: str | None) -> set[int]:
    ids: set[int] = set()
    if not raw:
        return ids
    for part in raw.replace(" ", ",").split(","):
        item = part.strip()
        if item.isdigit():
            ids.add(int(item))
    return ids


def is_allowed_bot_test_message(
    *,
    author_is_bot: bool,
    author_id: int,
    content: str,
    nonce: str | None,
    allowed_author_ids: set[int],
) -> bool:
    return bool(
        author_is_bot
        and nonce
        and author_id in allowed_author_ids
        and nonce in content
    )


def strip_test_nonce(content: str, nonce: str | None) -> str:
    if not nonce:
        return content
    return content.replace(nonce, "", 1).strip()
