#!/bin/bash
# Install Discord bots as LaunchAgents (user scope, GUI session so keychain works).
# Idempotent: can be re-run after updates.
#
# Env vars:
#   DRY_RUN=1  print what would happen, don't touch launchd
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_NAME="$(whoami)"
UID_NUM="$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
CONFIG="$PROJECT_DIR/config.json"

test -f "$CONFIG" || { echo "config.json not found at $CONFIG" >&2; exit 1; }

BOTS=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["bots"].keys()))' "$CONFIG")

run() {
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would run: $*"
  else
    "$@"
  fi
}

echo ">>> 1. 既存 bot プロセスを停止"
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "would run: pkill -9 -f 'bot.py'"
else
  pkill -9 -f 'bot.py' 2>/dev/null || true
  sleep 2
fi

echo ">>> 2. plist を生成（全 bot）"
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "would run: OUT_DIR=$AGENTS_DIR bash $SCRIPT_DIR/generate-plists.sh"
  echo "  (would write one plist per bot: $BOTS)"
else
  OUT_DIR="$AGENTS_DIR" bash "$SCRIPT_DIR/generate-plists.sh"
fi

echo ">>> 3. 既存 LaunchAgent bootout（あれば）"
for bot in $BOTS; do
  label="com.$USER_NAME.discord-bot-$bot"
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would run: launchctl bootout gui/$UID_NUM/$label (if loaded)"
  else
    if launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1; then
      launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
      sleep 1
    fi
  fi
done

echo ">>> 4. LaunchAgent bootstrap + kickstart"
for bot in $BOTS; do
  plist="$AGENTS_DIR/com.$USER_NAME.discord-bot-$bot.plist"
  label="com.$USER_NAME.discord-bot-$bot"
  run launchctl bootstrap "gui/$UID_NUM" "$plist"
  run launchctl kickstart -k "gui/$UID_NUM/$label"
done

if [ "${DRY_RUN:-}" = "1" ]; then
  echo "=== DRY RUN complete ==="
  exit 0
fi

echo ">>> 5. 起動確認（4秒待つ）"
sleep 4
echo "--- bot.py processes ---"
ps aux | grep 'bot.py' | grep -v grep | awk '{print $2, $11, $12, $13, $14}' || true

echo "--- launchctl list ---"
launchctl list | grep "com.$USER_NAME.discord-bot" || echo "WARNING: no agents listed"

echo "Done."
