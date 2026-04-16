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

BOTS=("general" "kb" "reserved")
TOKEN_ACCOUNTS=("general-bot-token" "kb-bot-token" "reserved-bot-token")

echo "=== Discord Bots (1bot=1project) Setup ==="
echo "User: $(whoami) | Home: $HOME"
echo ""

# 1. Python3 チェック
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 が見つかりません: brew install python3"
    exit 1
fi
echo "[OK] python3: $(python3 --version)"

# 2. Claude CLI チェック
if command -v claude &>/dev/null; then
    echo "[OK] claude: $(claude --version 2>/dev/null || echo 'installed')"
else
    echo "[WARN] claude CLI が見つかりません:"
    echo "  npm install -g @anthropic-ai/claude-code"
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

# 6. 旧 bot を停止
echo ""
for BOT in "${BOTS[@]}"; do
    LABEL="com.akimare.bot-${BOT}"
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
        echo "[SETUP] 旧 $BOT bot を停止中..."
        launchctl unload "$LAUNCHD_DST/${LABEL}.plist" 2>/dev/null || true
    fi
done
# 旧統合版も停止
if launchctl list 2>/dev/null | grep -q "com.akimare.discord-bot"; then
    launchctl unload "$LAUNCHD_DST/com.akimare.discord-bot.plist" 2>/dev/null || true
fi

# 7. plist をユーザーのホームに合わせて生成 & 登録
echo "[SETUP] launchd に登録中..."
for BOT in "${BOTS[@]}"; do
    LABEL="com.akimare.bot-${BOT}"
    SRC="$LAUNCHD_SRC/${LABEL}.plist"
    DST="$LAUNCHD_DST/${LABEL}.plist"

    if [ ! -f "$SRC" ]; then
        echo "[WARN] $SRC が見つかりません、スキップ"
        continue
    fi

    sed "s|/Users/akimare|$HOME|g" "$SRC" > "$DST"
    launchctl load "$DST"
    echo "  ✅ $BOT -> $LABEL"
done

echo ""
echo "=== セットアップ完了！3 bots 起動 ==="
echo ""
echo "確認:"
echo "  launchctl list | grep 'com.akimare.bot'  # 全bot確認"
echo "  tail -f ~/discord-bots/logs/general.log   # generalログ"
echo "  tail -f ~/discord-bots/logs/kb.log        # kbログ"
echo "  tail -f ~/discord-bots/logs/reserved.log  # reservedログ"
echo ""
echo "全停止:"
echo "  for b in general kb reserved; do launchctl unload ~/Library/LaunchAgents/com.akimare.bot-\$b.plist; done"
echo ""
echo "全再起動:"
echo "  cd ~/discord-bots && git pull && for b in general kb reserved; do launchctl unload ~/Library/LaunchAgents/com.akimare.bot-\$b.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/com.akimare.bot-\$b.plist; done"
