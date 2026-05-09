# issue-0004: General Bot duplicate Claude runs

Date: 2026-05-09

## Problem

General Bot sometimes created overlapping Claude Code runs for one Discord
thread. Symptoms were duplicate replies, multiple near-simultaneous Claude
processes using the same session id, and occasional no-response/error behavior.

## Findings

- `logs/general.err.log` showed repeated concurrent `claude --resume <same session>`
  launches for the same thread.
- Discord message events can be handled concurrently, but `bot.py` had no
  per-thread async lock around Claude invocation or session JSON updates.
- Old `com.akimare.*` LaunchAgent plist files still existed alongside the newer
  `com.kazuki-macmini.discord-bot-*` agents, creating a login-time duplicate
  startup risk.
- `start-bots.command` still referenced the removed `reserved` bot and only
  started three bots.
- Live Discord inspection on 2026-05-09 showed General Bot replied three times
  with `This thread has no active session...` in KB thread `1502503424476971088`.
  That exact text comes from the deprecated Node `bot.js`, not current Python
  `bot.py`.
- KB Bot also ran multiple Claude sessions at once and spammed Discord typing
  calls, causing HTTP 429 rate limits during long-running work.

## Changes

- Added per-thread `asyncio.Lock` guards around new-session and continued-thread
  Claude runs in `bot.py`.
- Kept the per-bot process singleton active at startup via `acquire_or_exit`.
- Added a per-bot Claude semaphore (`claude_max_concurrent_runs`, default 1) so
  one bot cannot launch several Claude Code runs in parallel.
- Increased typing interval to reduce Discord typing endpoint rate limits.
- Disabled deprecated Node `bot.js` unless `ALLOW_LEGACY_BOT_JS=1` is explicitly
  set, and made `start-all.sh` fail closed.
- Applied the same fail-closed guard to the stale `discord-bots-codex` copy.
- Hardened `launchd/install-macmini.sh` to remove legacy `com.akimare.*` bot
  agents and only kill this project's bot.py processes.
- Updated `start-bots.command` to start the current four bots.
- Reinstalled LaunchAgents and confirmed exactly one process for each bot.

## Verification

- `python -m py_compile bot.py singleton_lock.py claude_errors.py mention_helpers.py attachments.py`
- `node --check bot.js`
- `pytest tests/test_singleton_lock.py tests/test_claude_errors.py tests/test_mention_helpers.py tests/test_attachments.py` → 28 passed
- `bash tests/test_generate_plists.sh && bash tests/test_install_macmini.sh` → passed
- `launchctl list` shows only the current four `com.kazuki-macmini.discord-bot-*` bot agents.
- Live Discord test: started General Bot with temporary test nonce, posted to
  General forum thread `1502505624750260355` from KB Bot, observed exactly one
  Claude launch and exactly one `OK` reply from General Bot. Test thread archived.
- Live Discord retest on 2026-05-09 after user approval:
  - General Bot handled two actual General forum threads
    (`1502528004989911173`, `1502528006390939762`) with exactly one reply each:
    `A` and `B`.
  - General Bot ignored a KB forum thread (`1502528008328581151`) with zero
    replies.
  - KB Bot handled two actual KB forum threads
    (`1502528332166856784`, `1502528333957828618`) with exactly one reply each:
    `A` and `B`.
  - KB Bot ignored a General forum thread (`1502528336000319539`) with zero
    replies.
  - All six test threads were archived, temporary session JSON entries were
    removed, and test KB worktrees/branches were deleted.
- Follow-up on 2026-05-09: an existing General-owned thread
  (`1502228897054462063`) still received three `This thread has no active
  session...` replies from General Bot. That message is still only present in
  the deprecated Node `bot.js` implementation, and no local `bot.js` process or
  launchd job was found, so a remaining external/stale runtime using the same
  General bot token is suspected.
- Hardened Python `bot.py` further:
  - Bots with a configured control forum now ignore existing session records
    when the Discord thread belongs to another forum.
  - In a bot's own forum, if the user explicitly mentions another bot and does
    not mention this bot, this bot ignores the message.
- Live Discord retest: posted a General forum thread that mentioned Kanojo Bot
  (`1502530649385336892`). General Bot accepted the test event, logged the
  thread check, launched no Claude run, produced zero replies, and the test
  thread was archived.
- Process verification after restart: only four Python `bot.py` processes were
  running; no local `bot.js` process was present.
- Chrome Discord live reproduction on 2026-05-09:
  - In existing thread `1502228897054462063`, posted a real user message that
    mentioned Kanojo Bot.
  - Kanojo Bot replied once.
  - General Bot replied three times with the deprecated Node-only text
    `This thread has no active session...`.
  - This proved a stale runtime outside the local launchd/Python process set
    still held the old General bot token.
- Rotated the General Bot token in Discord Developer Portal after user MFA,
  saved the new token to macOS Keychain (`general-bot-token` /
  `discord-bot`), removed the stale `general-bot-token=` entry from `.env`,
  and restarted the General LaunchAgent.
- Verified the new General token with Discord API `/users/@me`, then confirmed
  the General LaunchAgent connected successfully.
- Post-rotation Chrome Discord retest in the same existing thread:
  - User message marker: `CODEX-ROTATION-RETEST-20260509-1356`.
  - History window after rotation contained exactly one user message and one
    Kanojo reply.
  - General Bot replies in the retest window: `0`.
  - This confirms the stale Node runtime was cut off by token rotation and the
    Python guard now ignores cross-bot mentions correctly.
- Verification after token rotation:
  - `python3 -m py_compile bot.py singleton_lock.py mention_helpers.py discord_test_auth.py attachments.py claude_errors.py`
  - `.venv/bin/python -m pytest tests/test_singleton_lock.py tests/test_claude_errors.py tests/test_mention_helpers.py tests/test_attachments.py tests/test_discord_test_auth.py` → 31 passed
  - `.venv/bin/python bot.py general` while launchd General was running → refused startup with singleton lock (`pid=40502`)
  - Process check showed exactly four Python `bot.py` processes and no local
    `bot.js` process.
