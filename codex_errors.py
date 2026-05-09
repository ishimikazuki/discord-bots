"""Helpers for turning Codex JSONL failures into readable text."""


def describe_codex_failure(
    returncode: int,
    stderr_text: str,
    last_event: dict | None,
) -> str:
    """Prefer stderr, then fall back to the last structured Codex event."""
    err = stderr_text.strip()
    if err:
        return err

    if last_event:
        event_type = last_event.get("type") or ""
        item = last_event.get("item") or {}
        if item.get("type") == "agent_message":
            text = str(item.get("text") or "").strip()
            if text:
                return text

        error = last_event.get("error") or last_event.get("message")
        if error:
            return str(error)

        if event_type:
            return f"Codex stopped after event={event_type}"

    return f"Codex exited {returncode}: (no stderr)"
