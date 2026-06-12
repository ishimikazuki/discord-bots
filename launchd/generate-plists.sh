#!/bin/bash
# Generate one LaunchAgent plist per bot listed in config.json.
# Idempotent: overwrites existing plists.
#
# Env vars (all optional):
#   OUT_DIR                    target dir (default: $HOME/Library/LaunchAgents)
#   USER_OVERRIDE              label prefix user (default: $(whoami))
#   HOME_OVERRIDE              base home dir (default: $HOME)
#   LIMIT_LOAD_TO_SESSION_TYPE "Background" for headless user launchd, otherwise unset
set -euo pipefail

OUT_DIR="${OUT_DIR:-$HOME/Library/LaunchAgents}"
USER_NAME="${USER_OVERRIDE:-$(whoami)}"
HOME_DIR="${HOME_OVERRIDE:-$HOME}"
LIMIT_LOAD_TO_SESSION_TYPE="${LIMIT_LOAD_TO_SESSION_TYPE:-}"
PROJECT_DIR="$HOME_DIR/discord-bots"
CONFIG="$PROJECT_DIR/config.json"

test -f "$CONFIG" || { echo "config.json not found at $CONFIG" >&2; exit 1; }

mkdir -p "$OUT_DIR"

BOTS=$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["bots"].keys()))' "$CONFIG")

for bot in $BOTS; do
  plist="$OUT_DIR/com.$USER_NAME.discord-bot-$bot.plist"
  session_xml=$'    <key>ProcessType</key>\n    <string>Interactive</string>'
  if [ -n "$LIMIT_LOAD_TO_SESSION_TYPE" ]; then
    session_xml="    <key>LimitLoadToSessionType</key>
    <string>$LIMIT_LOAD_TO_SESSION_TYPE</string>"
  fi
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.$USER_NAME.discord-bot-$bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PROJECT_DIR/.venv/bin/python</string>
        <string>-u</string>
        <string>$PROJECT_DIR/bot.py</string>
        <string>$bot</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/$bot.out.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/$bot.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME_DIR/.npm-global/bin:$HOME_DIR/.local/bin:$HOME_DIR/.local/node-v22/bin:/Applications/Codex.app/Contents/Resources:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>HOME</key>
        <string>$HOME_DIR</string>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
$session_xml
</dict>
</plist>
EOF
  echo "wrote $plist"
done
