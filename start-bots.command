#!/bin/bash
# Discord Bots 起動スクリプト（GUI Terminal 用）
# ダブルクリックまたはログイン項目で自動起動

cd ~/discord-bots
export PYTHONUNBUFFERED=1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$HOME/.local/node-v22/bin:/Applications/Codex.app/Contents/Resources:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 既存のbotプロセスを停止
pkill -f 'bot.py' 2>/dev/null
sleep 1

# 4つのbotをバックグラウンドで起動
.venv/bin/python -u bot.py general 2>&1 | tee logs/general-live.log &
.venv/bin/python -u bot.py kb 2>&1 | tee logs/kb-live.log &
.venv/bin/python -u bot.py kanojo 2>&1 | tee logs/kanojo-live.log &
.venv/bin/python -u bot.py yumekano-coe 2>&1 | tee logs/yumekano-coe-live.log &

echo "=== 4 bots started ==="
echo "general PID: $(pgrep -f 'bot.py general')"
echo "kb PID: $(pgrep -f 'bot.py kb')"
echo "kanojo PID: $(pgrep -f 'bot.py kanojo')"
echo "yumekano-coe PID: $(pgrep -f 'bot.py yumekano-coe')"
echo ""
echo "Stop: pkill -f 'bot.py'"
echo "Logs: tail -f logs/*-live.log"

# ウィンドウを開いたままにする
wait
