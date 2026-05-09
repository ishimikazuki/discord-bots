#!/bin/bash
# ============================================================================
# Discord Bots (1bot=1project) - Mac mini セットアップスクリプト
# ============================================================================
# 使い方: Mac mini 上で実行
#   cd ~/discord-bots && bash setup-macmini.sh
# ============================================================================

set -euo pipefail

BOTDIR="$HOME/discord-bots"
VENV="$BOTDIR/.venv"
LAUNCHD_SRC="$BOTDIR/launchd"
LAUNCHD_DST="$HOME/Library/LaunchAgents"
CONFIG="$BOTDIR/config.json"

BOTS=()
TOKEN_ACCOUNTS=()

echo "=== Discord Bots (1bot=1project) Setup ==="
echo "User: $(whoami) | Home: $HOME"
echo ""

# 1. Python3 チェック
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 が見つかりません: brew install python3"
    exit 1
fi
echo "[OK] python3: $(python3 --version)"

if [ ! -f "$CONFIG" ]; then
    echo "[ERROR] config.json が見つかりません: $CONFIG"
    exit 1
fi
BOTS_STR=$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(" ".join(data["bots"].keys()))' "$CONFIG")
TOKEN_ACCOUNTS_STR=$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(" ".join(bot["token_keychain_account"] for bot in data["bots"].values()))' "$CONFIG")
IFS=' ' read -r -a BOTS <<< "$BOTS_STR"
IFS=' ' read -r -a TOKEN_ACCOUNTS <<< "$TOKEN_ACCOUNTS_STR"

# 2. Codex CLI チェック
if command -v codex &>/dev/null; then
    echo "[OK] codex: $(codex --version 2>/dev/null || echo 'installed')"
else
    echo "[WARN] codex CLI が見つかりません:"
    echo "  npm install -g @openai/codex"
fi

# 3. venv 作成 & 依存インストール
if [ ! -d "$VENV" ]; then
    echo "[SETUP] Python venv を作成中..."
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$BOTDIR/requirements.txt" -q
echo "[OK] venv + discord.py"

# 4. ログディレクトリ
mkdir -p "$BOTDIR/logs"

# 5. Keychain トークン確認
echo ""
MISSING_TOKENS=0
for i in "${!BOTS[@]}"; do
    BOT="${BOTS[$i]}"
    ACCOUNT="${TOKEN_ACCOUNTS[$i]}"
    if security find-generic-password -a "$ACCOUNT" -s "discord-bot" -w &>/dev/null; then
        echo "[OK] $BOT token: registered"
    else
        echo "[WARN] $BOT token ($ACCOUNT) が未登録:"
        echo "  security add-generic-password -a \"$ACCOUNT\" -s \"discord-bot\" -w \"YOUR_TOKEN\""
        MISSING_TOKENS=$((MISSING_TOKENS + 1))
    fi
done

if [ "$MISSING_TOKENS" -gt 0 ]; then
    echo ""
    echo "[ERROR] $MISSING_TOKENS 個のトークンが未登録。登録後に再実行してください。"
    exit 1
fi

# 6. plist 生成・LaunchAgent 登録
echo ""
echo "[SETUP] launchd に登録中..."
bash "$LAUNCHD_SRC/install-macmini.sh"

echo ""
echo "=== セットアップ完了！${#BOTS[@]} bots 起動 ==="
echo ""
echo "確認:"
echo "  launchctl list | grep 'discord-bot'       # 全bot確認"
echo "  tail -f ~/discord-bots/logs/general.err.log"
echo ""
echo "全停止:"
echo "  pkill -f '~/discord-bots/bot.py'"
echo ""
echo "全再起動:"
echo "  cd ~/discord-bots && git pull && bash launchd/install-macmini.sh"
