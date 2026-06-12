#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$PROJECT_ROOT/launchd/install-macmini.sh"

OUT=$(DRY_RUN=1 LAUNCHD_TARGET_DOMAIN=gui/501 bash "$SCRIPT" 2>&1)

echo "$OUT" | grep -q "target domain: gui/501" || { echo "FAIL: no gui target domain"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would run: launchctl bootstrap gui/501" || { echo "FAIL: no bootstrap message"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would run: launchctl kickstart" || { echo "FAIL: no kickstart message"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com\..*\.discord-bot-general" || { echo "FAIL: no general in dry-run output"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com\..*\.discord-bot-yumekano-coe" || { echo "FAIL: no yumekano-coe in dry-run output"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com.akimare.discord.general-bot" || { echo "FAIL: legacy node general bot not cleaned"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com.akimare.discord.knowledge-hub-bot" || { echo "FAIL: legacy node kb bot not cleaned"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com.akimare.discord.reserved-bot" || { echo "FAIL: legacy node reserved bot not cleaned"; echo "$OUT"; exit 1; }

BG_OUT=$(DRY_RUN=1 LAUNCHD_TARGET_DOMAIN=user/501 bash "$SCRIPT" 2>&1)
echo "$BG_OUT" | grep -q "target domain: user/501" || { echo "FAIL: no background target domain"; echo "$BG_OUT"; exit 1; }
echo "$BG_OUT" | grep -q "limit load session type: Background" || { echo "FAIL: no background session type"; echo "$BG_OUT"; exit 1; }
echo "$BG_OUT" | grep -q "would run: launchctl bootstrap user/501" || { echo "FAIL: no background bootstrap message"; echo "$BG_OUT"; exit 1; }

# Non-dry-run would touch system; skip
echo "PASS (dry-run)"
