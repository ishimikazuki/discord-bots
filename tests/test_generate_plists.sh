#!/bin/bash
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/launchd/generate-plists.sh"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TMPDIR_OUT=$(mktemp -d)
TMPDIR_HOME=$(mktemp -d)
trap "rm -rf $TMPDIR_OUT $TMPDIR_HOME" EXIT

# Seed a fake home with a discord-bots dir that has the real config.json
mkdir -p "$TMPDIR_HOME/discord-bots"
cp "$PROJECT_ROOT/config.json" "$TMPDIR_HOME/discord-bots/"

OUT_DIR="$TMPDIR_OUT" HOME_OVERRIDE="$TMPDIR_HOME" USER_OVERRIDE=testuser \
  bash "$SCRIPT"

# Every bot in config.json must have a plist
for bot in $(python3 -c '
import json, sys
with open("'"$TMPDIR_HOME/discord-bots/config.json"'") as f:
    print(" ".join(json.load(f)["bots"].keys()))
'); do
  plist="$TMPDIR_OUT/com.testuser.discord-bot-$bot.plist"
  test -f "$plist" || { echo "FAIL: $plist missing"; exit 1; }
  grep -q "$TMPDIR_HOME/discord-bots/.venv/bin/python" "$plist" || { echo "FAIL: python path for $bot"; exit 1; }
  grep -q "/bot.py</string>" "$plist" || { echo "FAIL: bot.py arg for $bot"; exit 1; }
  grep -q "<string>$bot</string>" "$plist" || { echo "FAIL: bot name $bot not in args"; exit 1; }
  grep -q "$TMPDIR_HOME/.npm-global/bin" "$plist" || { echo "FAIL: PATH missing npm-global for $bot"; exit 1; }
  grep -q "<key>KeepAlive</key>" "$plist" || { echo "FAIL: KeepAlive missing for $bot"; exit 1; }
  grep -q "<key>RunAtLoad</key>" "$plist" || { echo "FAIL: RunAtLoad missing for $bot"; exit 1; }
  grep -q "<key>ProcessType</key>" "$plist" || { echo "FAIL: ProcessType missing for $bot"; exit 1; }
done

OUT_DIR="$TMPDIR_OUT/background" HOME_OVERRIDE="$TMPDIR_HOME" USER_OVERRIDE=testuser LIMIT_LOAD_TO_SESSION_TYPE=Background \
  bash "$SCRIPT"
bg_plist="$TMPDIR_OUT/background/com.testuser.discord-bot-general.plist"
grep -q "<key>LimitLoadToSessionType</key>" "$bg_plist" || { echo "FAIL: background session key missing"; exit 1; }
grep -q "<string>Background</string>" "$bg_plist" || { echo "FAIL: background session value missing"; exit 1; }
! grep -q "<key>ProcessType</key>" "$bg_plist" || { echo "FAIL: background plist should not use ProcessType"; exit 1; }

# Sanity: at least 4 bots (general, kb, reserved, yumekano-coe)
count=$(ls "$TMPDIR_OUT"/com.testuser.discord-bot-*.plist | wc -l | tr -d ' ')
test "$count" -ge 4 || { echo "FAIL: expected >=4 plists, got $count"; exit 1; }

echo "PASS ($count plists)"
