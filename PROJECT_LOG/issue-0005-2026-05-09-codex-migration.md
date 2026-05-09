# issue-0005: Discord Bot Codex migration

Date: 2026-05-09

## Summary

Moved the Discord Bot runtime from Claude Code CLI to Codex CLI.

## Changes

- Replaced `bot.py` agent execution with `codex exec --json`.
- Added Codex JSONL parsing for `thread.started`, `item.completed` agent messages, and `turn.completed` usage.
- Stored `"agent": "codex"` in new session records and kanojo summary sessions.
- Added a legacy-session guard: old sessions with non-Codex `sessionId` cannot be resumed and ask the user to start a new thread.
- Renamed failure helper/tests from Claude-specific to Codex-specific.
- Updated launchd PATH generation and GUI start scripts so `/Applications/Codex.app/Contents/Resources/codex` is available.
- Migrated the diary reminder script in `knowledge-hub` to call Codex.

## Verification

- `codex exec --json` new-session smoke test returned a `thread.started` event and an agent message.
- `codex exec resume --json <thread_id> -` resume smoke test returned the same thread id and an agent message.
- `python -m py_compile bot.py singleton_lock.py mention_helpers.py discord_test_auth.py attachments.py codex_errors.py card_summary/scheduler.py card_summary/gmail_fetcher.py`
- `node --check bot.js`
- `pytest tests/test_singleton_lock.py tests/test_codex_errors.py tests/test_mention_helpers.py tests/test_attachments.py tests/test_discord_test_auth.py` -> 31 passed.

## Caveat

Existing Claude Code session ids are not portable to Codex. They remain in the session JSON for auditability, but users must start a new Discord thread to continue with Codex.
