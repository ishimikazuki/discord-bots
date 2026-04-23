# Role mention 対応 + LaunchAgent 常駐化

## Goal
- #一般 でのロールメンションから bot 専用フォーラムへ自動でスレッドを立てる
- macmini 上の bot を LaunchAgent で恒久化し keychain access を維持

## Done
- `mention_helpers.py` 新設（is_bot_addressed / strip_mentions、12 tests green）
- `bot.py` を更新: `on_message` で user/role 両方のメンションを判定、`forum.create_thread` で専用フォーラムに投稿、#一般 にリンク返信
- `launchd/generate-plists.sh` を新設（config.json 駆動で冪等生成）
- `launchd/install-macmini.sh` を新設（DRY_RUN 対応、bootout→bootstrap→kickstart）
- macmini の `~/Library/LaunchAgents/` に 4 bot 分の `com.kazuki-macmini.discord-bot-*.plist` を展開
- `launchctl bootstrap gui/$UID` で GUI セッションにロード、全 bot PPID=1
- E2E 検証: Discord メンション→フォーラムスレッド→Claude 応答（$0.1000, 147 chars）
- 再起動耐性: kill 後 launchd が自動で新 PID で再起動

## Discoveries
- SSH nohup で起動した python は login keychain を読めない（`errSecInteractionNotAllowed` / exit 36）。結果 Claude Code CLI が "Not logged in · Please run /login" で落ちる
- `osascript → Terminal.app` 経由起動でも GUI 文脈で動くが、Terminal 終了で一緒に死ぬ
- LaunchAgent + `launchctl bootstrap gui/$UID` のみが「GUI session 文脈 + 親 launchd で auto-restart」を両立
- Discord desktop の Slate rich-text editor は agent-browser の `keyboard type` / `inserttext` に反応しにくいが、文字入力は蓄積されており Enter で送信される
- ロール `@knowledge-hub-bot` は `<@&1493204478902534255>` のフォーマット。ユーザーメンション `<@userID>` と別処理が必要

## Decisions
- bot 専用フォーラム（案A）採用。共通ロールだと全 bot 一斉反応で会話が混線するため却下
- plist 命名: `com.kazuki-macmini.discord-bot-*`。旧 `com.akimare.*` は破棄対象だが今回は残置
- plist 生成は config.json を single source of truth とし、bot 追加時も再生成で対応

## Notes
- 旧 `~/Library/LaunchAgents/com.akimare.bot-*.plist` は未ロードなので害はないが、掃除しても良い
- git pull 時の "Device not configured" は別件（macmini に GitHub auth なし）
- E2E メンション送信は agent-browser では不安定。運用時はユーザーが直接 Discord で実行
