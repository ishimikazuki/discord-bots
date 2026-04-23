"""Pure helpers for detecting whether a bot is addressed and stripping
its mentions from message content."""
import re


def is_bot_addressed(
    user_mention_ids: set[int],
    role_mention_ids: set[int],
    bot_user_id: int,
    bot_role_ids: set[int],
) -> bool:
    """Return True if the bot itself or any role it has is mentioned."""
    if bot_user_id in user_mention_ids:
        return True
    if role_mention_ids & bot_role_ids:
        return True
    return False


def strip_mentions(content: str, user_ids: set[int], role_ids: set[int]) -> str:
    """Remove `<@id>`, `<@!id>`, and `<@&id>` tokens for the given ids.
    Unrelated mentions are left intact."""
    text = content
    for uid in user_ids:
        text = re.sub(rf"<@!?{uid}>", "", text)
    for rid in role_ids:
        text = re.sub(rf"<@&{rid}>", "", text)
    return text.strip()
