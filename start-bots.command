#!/bin/bash
# Discord Bots 起動スクリプト（GUI Terminal 用）
# ダブルクリックまたはログイン項目で自動起動

cd ~/discord-bots
export PYTHONUNBUFFERED=1

# 既存のbotプロセスを停止
pkill -f 'bot.py' 2>/dev/null
sleep 1

# 3つのbotをバックグラウンドで起動
.venv/bin/python -u bot.py general 2>&1 | tee logs/general-live.log &
.venv/bin/python -u bot.py kb 2>&1 | tee logs/kb-live.log &
.venv/bin/python -u bot.py reserved 2>&1 | tee logs/reserved-live.log &

echo "=== 3 bots started ==="
echo "general PID: $(pgrep -f 'bot.py general')"
echo "kb PID: $(pgrep -f 'bot.py kb')"
echo "reserved PID: $(pgrep -f 'bot.py reserved')"
echo ""
echo "Stop: pkill -f 'bot.py'"
echo "Logs: tail -f logs/*-live.log"

# ウィンドウを開いたままにする
wait
