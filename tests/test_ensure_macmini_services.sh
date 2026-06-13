#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$PROJECT_ROOT/launchd/ensure-macmini-services.sh"

OUT=$(DRY_RUN=1 LAUNCHD_TARGET_DOMAIN=user/501 TAILSCALE_BIN=/Applications/Tailscale.app/Contents/MacOS/Tailscale bash "$SCRIPT" 2>&1)

echo "$OUT" | grep -q "target domain: user/501" || { echo "FAIL: no user target domain"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would check: /Applications/Tailscale.app/Contents/MacOS/Tailscale status" || { echo "FAIL: no Tailscale check"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would run: /Applications/Tailscale.app/Contents/MacOS/Tailscale up" || { echo "FAIL: no Tailscale up"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "LIMIT_LOAD_TO_SESSION_TYPE=Background" || { echo "FAIL: no background plist generation"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would check: launchctl print user/501/com\..*\.discord-bot-general" || { echo "FAIL: no general launchctl check"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would run: launchctl bootstrap user/501" || { echo "FAIL: no bootstrap dry-run"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would run: launchctl kickstart user/501/com\..*\.discord-bot-general" || { echo "FAIL: no kickstart dry-run"; echo "$OUT"; exit 1; }

echo "PASS (dry-run)"
