"""Helpers for turning Claude Code stream-json failures into readable text."""


def describe_claude_failure(
    returncode: int,
    stderr_text: str,
    result_event: dict | None,
) -> str:
    """Prefer the structured result error when Claude exits non-zero.

    Claude Code can return exit 1 with an empty stderr while still emitting a
    stream-json result event that contains the actual API/authentication error.
    """
    err = stderr_text.strip()
    if err:
        return err

    if result_event:
        subtype = result_event.get("subtype") or ""
        num_turns = result_event.get("num_turns")
        duration_ms = result_event.get("duration_ms")

        if subtype == "error_max_turns":
            secs = round(duration_ms / 1000) if isinstance(duration_ms, (int, float)) else "?"
            return (
                f"ターン上限に達したよ (num_turns={num_turns}, {secs}s)。"
                f"config.json の claude_max_turns を増やすか、タスクを分割してね。"
            )

        result = str(result_event.get("result") or "").strip()
        if result:
            status = result_event.get("api_error_status")
            prefix = f"API {status}: " if status else ""
            return f"{prefix}{result}"

        if subtype:
            return f"Claude Code stopped: subtype={subtype} num_turns={num_turns}"

    return f"Claude Code exited {returncode}: (no stderr)"
