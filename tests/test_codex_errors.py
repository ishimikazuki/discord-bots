import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codex_errors import describe_codex_failure


def test_describe_codex_failure_prefers_stderr():
    assert describe_codex_failure(1, "boom", {"message": "api failed"}) == "boom"


def test_describe_codex_failure_uses_event_message_when_stderr_empty():
    event = {
        "type": "error",
        "message": "Failed to authenticate",
    }
    assert describe_codex_failure(1, "", event) == "Failed to authenticate"


def test_describe_codex_failure_keeps_no_stderr_fallback():
    assert describe_codex_failure(1, "", None) == "Codex exited 1: (no stderr)"
