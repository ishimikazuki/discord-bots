# issue-0006: Codex migration verification

Date: 2026-05-09

## Summary

Added regression coverage for the Claude Code to Codex replacement, focusing on the Discord Bot paths that matter for daily use: card summaries, schedule/calendar work, file handoff, historical thread prompts, and legacy session safety.

## Changes

- Added `tests/test_codex_migration.py`.
- Covered Codex CLI argument construction, JSONL parsing, stdin prompt handoff, resume handling, and error reporting.
- Covered the card-summary thread path: new summary threads are registered as Codex sessions and first replies receive the summary context.
- Covered historical kanojo schedule-like thread titles by replaying them through the Codex prompt builder.
- Covered the legacy Claude-session guard so old `sessionId` values are not mistakenly resumed with Codex.
- Verified runtime files no longer call `claude --`.
- In `~/kanojo`, committed and pushed `.agents/skills -> ../.claude/skills` plus a Codex wording update for `screenshot-to-calendar`, so new Codex worktrees can see the same project-local schedule screenshot skill.

## Verification

- `pytest tests/test_codex_migration.py -q` -> 10 passed.
- `pytest -q` in `~/discord-bots` -> 98 passed.
- `pytest -q` in `~/kanojo` -> 91 passed.
- `codex debug prompt-input` in `~/kanojo` shows `screenshot-to-calendar` as an available skill.
- `python -m core.recalc --dry-run` in `~/kanojo` completed without applying changes: 4 target events, 4 desired auto-events, 4 existing auto-events.
- launchd reports all four Discord Bot services running: general, kb, kanojo, yumekano-coe.

## Caveat

Tests avoid externally visible writes: they do not create real Discord test threads or insert Google Calendar events. Calendar mutation remains gated by the existing confirmation flow in `screenshot-to-calendar`.
