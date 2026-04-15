#!/bin/bash
# 3つのDiscord Botを起動
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting all Discord bots..."
node "$DIR/bot.js" knowledge-hub-bot &
node "$DIR/bot.js" general-bot &
node "$DIR/bot.js" reserved-bot &
echo "All bots started. PIDs: $(jobs -p)"
wait
