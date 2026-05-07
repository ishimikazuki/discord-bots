import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from claude_errors import describe_claude_failure


def test_describe_claude_failure_prefers_stderr():
    assert describe_claude_failure(1, "boom", {"result": "api failed"}) == "boom"


def test_describe_claude_failure_uses_result_event_when_stderr_empty():
    event = {
        "is_error": True,
        "api_error_status": 401,
        "result": "Failed to authenticate. API Error: 401 invalid",
    }
    assert describe_claude_failure(1, "", event).startswith("API 401: Failed")


def test_describe_claude_failure_keeps_no_stderr_fallback():
    assert describe_claude_failure(1, "", None) == "Claude Code exited 1: (no stderr)"
