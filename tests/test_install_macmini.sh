#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$PROJECT_ROOT/launchd/install-macmini.sh"

OUT=$(DRY_RUN=1 bash "$SCRIPT" 2>&1)

echo "$OUT" | grep -q "would run: launchctl bootstrap gui" || { echo "FAIL: no bootstrap message"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "would run: launchctl kickstart" || { echo "FAIL: no kickstart message"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com\..*\.discord-bot-general" || { echo "FAIL: no general in dry-run output"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "com\..*\.discord-bot-yumekano-coe" || { echo "FAIL: no yumekano-coe in dry-run output"; echo "$OUT"; exit 1; }

# Non-dry-run would touch system; skip
echo "PASS (dry-run)"
