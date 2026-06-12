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

kickstart_agent() {
  local target="$1"
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would run: launchctl kickstart -k $target"
    return 0
  fi

  launchctl kickstart -k "$target" &
  local pid=$!
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge 8 ]; then
      echo "WARNING: launchctl kickstart timed out for $target; continuing" >&2
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" || echo "WARNING: launchctl kickstart failed for $target" >&2
}

echo ">>> 1. 既存 bot プロセスを停止"
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "would run: pkill -9 -f '$PROJECT_DIR/bot.py'"
else
  pkill -9 -f "$PROJECT_DIR/bot.py" 2>/dev/null || true
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
legacy_labels=(
  "com.akimare.discord-bot"
  "com.akimare.discord.general-bot"
  "com.akimare.discord.knowledge-hub-bot"
  "com.akimare.discord.reserved-bot"
  "com.akimare.bot-general"
  "com.akimare.bot-kb"
  "com.akimare.bot-reserved"
  "com.akimare.bot-yumekano-coe"
)
for label in "${legacy_labels[@]}"; do
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would run: launchctl bootout gui/$UID_NUM/$label (if loaded, legacy)"
  else
    if launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1; then
      launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
      sleep 1
    fi
    rm -f "$AGENTS_DIR/$label.plist"
  fi
done

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
  kickstart_agent "gui/$UID_NUM/$label"
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
