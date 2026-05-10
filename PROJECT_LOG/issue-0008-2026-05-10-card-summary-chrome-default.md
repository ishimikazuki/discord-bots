# issue-0008: Card summary checks Epos through trusted Chrome at every slot

## Summary
- Corrected the Epos Net automation path from Playwright-first to trusted Google Chrome by default.
- Moved Epos refresh into each daily summary slot: 7:00, 15:00, and 22:00.
- Kept notification behavior quiet unless the report state changes.

## Changes
- `card_summary/epos_scraper.py`
  - `fetch_month_history()` now always uses the macmini's trusted Google Chrome profile via `osascript`.
  - Removed the Playwright implementation and storage-state dependency from the default runtime.
- `card_summary/scheduler.py`
  - Calls `run_reconciliation()` at the start of every slot before computing/posting the summary.
  - Stops starting a separate daily 03:00 reconciliation loop by default.
  - Keeps the legacy/manual reconciliation loop available but unused by the live scheduler.
- `requirements.txt`
  - Removed the Playwright package dependency.
- Tests
  - Added coverage that `fetch_month_history()` delegates to trusted Chrome.
  - Added coverage that `run_slot()` refreshes Epos data before reporting.

## Notification Contract
- The bot checks at 7:00, 15:00, and 22:00.
- It posts only when at least one of these changes from the previous state for that slot:
  - current-month total
  - category breakdown hash
  - max transaction id
  - alert hash
- Alerts remain:
  - current-month pace exceeds previous-month total by 30%+
  - category total exceeds previous-month same-day category total by 2x+
  - today's largest transaction exceeds the last-30-day median by 5x+

## Tests
- Focused card-summary tests: 27 passed
- Full test suite: 113 passed
- Syntax checks:
  - `py_compile` passed for bot and card_summary modules
  - `node --check bot.js` passed

## Live Verification
- Codex Chrome Extension could open the logged-in Epos history page and read current rows.
- Runtime Chrome scrape through the trusted profile:
  - 2026-05: 20 rows, 52,943 yen
  - 2026-04: 82 rows, 291,471 yen
