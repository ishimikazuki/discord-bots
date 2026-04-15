#!/bin/bash
# ============================================================================
# Discord Bot (Python) - Mac mini セットアップスクリプト
# ============================================================================
# 使い方: Mac mini 上で実行
#   cd ~/discord-bots && bash setup-macmini.sh
# ============================================================================

set -euo pipefail

BOTDIR="$HOME/discord-bots"
VENV="$BOTDIR/.venv"
PLIST_SRC="$BOTDIR/com.akimare.discord-bot-py.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.akimare.discord-bot.plist"

echo "=== Discord Bot (Python) Setup ==="

# 1. Python3 チェック
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 が見つかりません。先にインストールしてください:"
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo "  brew install python3"
    exit 1
fi
echo "[OK] python3: $(python3 --version)"

# 2. Claude CLI チェック
if ! command -v claude &>/dev/null; then
    echo "[WARN] claude CLI が見つかりません。npm でインストールしてください:"
    echo "  npm install -g @anthropic-ai/claude-code"
    echo "  （または brew install node → npm install -g @anthropic-ai/claude-code）"
fi

# 3. venv 作成
if [ ! -d "$VENV" ]; then
    echo "[SETUP] Python venv を作成中..."
    python3 -m venv "$VENV"
fi
echo "[OK] venv: $VENV"

# 4. 依存インストール
echo "[SETUP] パッケージをインストール中..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$BOTDIR/requirements.txt" -q
echo "[OK] discord.py インストール完了"

# 5. ログディレクトリ
mkdir -p "$BOTDIR/logs"

# 6. Keychain にトークンが登録されているか確認
if ! security find-generic-password -a "general-bot-token" -s "discord-bot" -w &>/dev/null; then
    echo ""
    echo "[WARN] キーチェーンに Discord bot トークンが登録されていません。"
    echo "  以下のコマンドで登録してください:"
    echo ""
    echo "  security add-generic-password -a \"general-bot-token\" -s \"discord-bot\" -w \"YOUR_TOKEN_HERE\""
    echo ""
fi

# 7. 旧 Node.js 版の launchd を停止（あれば）
if launchctl list | grep -q "com.akimare.discord-bot" 2>/dev/null; then
    echo "[SETUP] 旧 bot を停止中..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# 8. plist をコピーして登録
echo "[SETUP] launchd に登録中..."
sed "s|/Users/akimare|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"
launchctl load "$PLIST_DST"

echo ""
echo "=== セットアップ完了！ ==="
echo ""
echo "確認コマンド:"
echo "  launchctl list | grep discord     # プロセス確認"
echo "  tail -f ~/discord-bots/logs/bot.log  # ログ確認"
echo ""
echo "停止:"
echo "  launchctl unload ~/Library/LaunchAgents/com.akimare.discord-bot.plist"
echo ""
echo "再起動:"
echo "  launchctl unload ~/Library/LaunchAgents/com.akimare.discord-bot.plist"
echo "  launchctl load ~/Library/LaunchAgents/com.akimare.discord-bot.plist"
