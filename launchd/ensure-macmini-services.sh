#!/bin/bash
# Keep Mac mini background services alive after reboot/headless startup.
#
# Intended for cron @reboot and periodic self-healing. Unlike install-macmini.sh,
# this script does not kill healthy bot processes.
#
# Env vars:
#   DRY_RUN=1                  print what would happen, don't touch services
#   LAUNCHD_TARGET_DOMAIN=...  override launchctl target, defaults to user/$UID
#   TAILSCALE_BIN=...          override Tailscale CLI path
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_NAME="$(whoami)"
UID_NUM="$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
CONFIG="$PROJECT_DIR/config.json"
TARGET_DOMAIN="${LAUNCHD_TARGET_DOMAIN:-user/$UID_NUM}"
TAILSCALE_BIN="${TAILSCALE_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"

test -f "$CONFIG" || { echo "config.json not found at $CONFIG" >&2; exit 1; }
mkdir -p "$PROJECT_DIR/logs" "$AGENTS_DIR"

BOTS=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["bots"].keys()))' "$CONFIG")

run() {
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would run: $*"
  else
    "$@"
  fi
}

ensure_tailscale() {
  echo ">>> Tailscale"
  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would check: $TAILSCALE_BIN status"
    echo "would run: $TAILSCALE_BIN up (if stopped/offline)"
    return 0
  fi

  if [ ! -x "$TAILSCALE_BIN" ]; then
    echo "WARNING: Tailscale CLI not found at $TAILSCALE_BIN" >&2
    return 0
  fi

  if "$TAILSCALE_BIN" status >/dev/null 2>&1; then
    echo "Tailscale online"
    return 0
  fi

  echo "Tailscale appears offline; running up"
  "$TAILSCALE_BIN" up || echo "WARNING: Tailscale up failed" >&2
}

ensure_plists() {
  local missing=0
  for bot in $BOTS; do
    local plist="$AGENTS_DIR/com.$USER_NAME.discord-bot-$bot.plist"
    if [ ! -f "$plist" ]; then
      missing=1
    fi
  done

  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would run: OUT_DIR=$AGENTS_DIR LIMIT_LOAD_TO_SESSION_TYPE=Background bash $SCRIPT_DIR/generate-plists.sh (if any plist missing)"
    return 0
  fi

  if [ "$missing" = "1" ]; then
    OUT_DIR="$AGENTS_DIR" LIMIT_LOAD_TO_SESSION_TYPE="Background" bash "$SCRIPT_DIR/generate-plists.sh"
  fi
}

service_running() {
  local target="$1"
  launchctl print "$target" 2>/dev/null | grep -q "state = running"
}

ensure_bot() {
  local bot="$1"
  local label="com.$USER_NAME.discord-bot-$bot"
  local plist="$AGENTS_DIR/$label.plist"
  local target="$TARGET_DOMAIN/$label"

  if [ "${DRY_RUN:-}" = "1" ]; then
    echo "would check: launchctl print $target"
    echo "would run: launchctl bootstrap $TARGET_DOMAIN $plist (if unloaded)"
    echo "would run: launchctl kickstart $target (if loaded but not running)"
    return 0
  fi

  if ! launchctl print "$target" >/dev/null 2>&1; then
    echo "bootstrap $target"
    launchctl bootstrap "$TARGET_DOMAIN" "$plist" || {
      echo "WARNING: bootstrap failed for $target" >&2
      return 0
    }
  fi

  if service_running "$target"; then
    echo "$target running"
  else
    echo "kickstart $target"
    launchctl kickstart "$target" || echo "WARNING: kickstart failed for $target" >&2
  fi
}

echo "=== ensure macmini services $(date '+%Y-%m-%d %H:%M:%S %z') ==="
echo "target domain: $TARGET_DOMAIN"

ensure_tailscale

echo ">>> Discord bot LaunchAgents"
ensure_plists
for bot in $BOTS; do
  ensure_bot "$bot"
done

echo "Done."
