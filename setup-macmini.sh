#!/bin/bash
# ============================================================================
# Discord Bot (Python v2) - Mac mini セットアップスクリプト
# ============================================================================
# 使い方: Mac mini 上で実行
#   cd ~/discord-bots && bash setup-macmini.sh
# ============================================================================

set -euo pipefail

BOTDIR="$HOME/discord-bots"
VENV="$BOTDIR/.venv"
PLIST_SRC="$BOTDIR/com.akimare.discord-bot-py.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.akimare.discord-bot.plist"

echo "=== Discord Bot (Python v2) Setup ==="
echo "User: $(whoami) | Home: $HOME"

# 1. Python3 チェック
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 が見つかりません:"
    echo "  brew install python3"
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

# 3. Git チェック
if ! command -v git &>/dev/null; then
    echo "[ERROR] git が見つかりません:"
    echo "  brew install git"
    exit 1
fi
echo "[OK] git: $(git --version)"

# 4. venv 作成
if [ ! -d "$VENV" ]; then
    echo "[SETUP] Python venv を作成中..."
    python3 -m venv "$VENV"
fi
echo "[OK] venv: $VENV"

# 5. 依存インストール
echo "[SETUP] パッケージをインストール中..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$BOTDIR/requirements.txt" -q
echo "[OK] discord.py インストール完了"

# 6. ログディレクトリ
mkdir -p "$BOTDIR/logs"

# 7. config.json 確認
if [ ! -f "$BOTDIR/config.json" ]; then
    echo "[ERROR] config.json が見つかりません。リポジトリを再 clone してください。"
    exit 1
fi
echo "[OK] config.json 存在確認"

# 8. Keychain にトークンが登録されているか確認
if ! security find-generic-password -a "general-bot-token" -s "discord-bot" -w &>/dev/null; then
    echo ""
    echo "[WARN] キーチェーンに Discord bot トークンが登録されていません。"
    echo "  以下のコマンドで登録してください:"
    echo ""
    echo "  security add-generic-password -a \"general-bot-token\" -s \"discord-bot\" -w \"YOUR_TOKEN_HERE\""
    echo ""
    echo "  ※ トークンを登録してからもう一度 setup-macmini.sh を実行してください。"
    exit 1
fi
echo "[OK] Keychain トークン確認済み"

# 9. 旧 bot の launchd を停止（あれば）
if launchctl list 2>/dev/null | grep -q "com.akimare.discord-bot"; then
    echo "[SETUP] 旧 bot を停止中..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# 10. plist をユーザーのホームに合わせて生成 & 登録
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
echo "更新 (git pull → 再起動):"
echo "  cd ~/discord-bots && git pull && launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
echo ""
echo "停止:"
echo "  launchctl unload $PLIST_DST"
