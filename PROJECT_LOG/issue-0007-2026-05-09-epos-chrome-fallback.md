# issue-0007: Epos Net Chrome fallback and duplicate-source-id repair

## Summary
- Fixed card_summary live Epos reconciliation when Playwright is rejected by Epos Net's session trust checks.
- Added a Google Chrome + AppleScript fallback that uses the already trusted macmini Chrome profile.
- Fixed `source_id` generation so new Epos rows inserted earlier in a month do not shift IDs for existing rows.

## Changes
- `card_summary/epos_scraper.py`
  - Keeps Playwright as the first attempt.
  - Falls back to trusted Chrome via `osascript` when Playwright raises `EposLoginChallengeError`.
  - Logs in with keychain credentials, handles optional CVV keypad, submits the target year/month form, and parses the monthly table.
  - Uses per-natural-key occurrence counts for `source_id`, instead of the row's absolute table position.
- `card_summary/scheduler.py`
  - Keeps Gmail failures non-fatal.
  - Allows one month of reconciliation to succeed even if the other month fails.
  - Runs startup reconciliation only when the Epos checkpoint is not already today.
  - Supports kanojo posting to either ForumChannel or TextChannel.
- `card_summary/config.py`
  - Sets kanojo control channel ID to `1497151379393876020`.

## Live Verification
- Enabled Chrome profile preference `browser.allow_javascript_apple_events=true`.
- Live Epos fetch:
  - 2026-05: 19 rows, 49,535 yen
  - 2026-04: 81 rows, 290,987 yen
- DB after repair:
  - 100 total rows, 340,522 yen
  - 2026-05: 19 rows, 49,535 yen
  - 2026-04: 81 rows, 290,987 yen
  - duplicate natural-key groups: 0
- Re-ran reconciliation after rekey:
  - `inserted=0`

## Tests
- `.venv/bin/python -m pytest -q` -> 111 passed
- `.venv/bin/python -m py_compile bot.py singleton_lock.py mention_helpers.py discord_test_auth.py attachments.py codex_errors.py card_summary/epos_scraper.py card_summary/scheduler.py card_summary/gmail_fetcher.py`
- `node --check bot.js`
