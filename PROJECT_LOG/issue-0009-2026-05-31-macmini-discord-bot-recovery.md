# Mac mini Discord bot 障害復旧

## Goal
- Mac mini 上の Discord bot 群が動いていない原因を SSH 経由で特定し、必要最小限で復旧する

## Done
- `macmini` (Tailscale) と `macmini-lan` (LAN) の SSH 到達性を確認した
- Mac mini が 2026-05-30 04:22 JST に再起動後、GUI ユーザー未ログイン状態であることを確認した
- `launchctl print gui/501` が使えず、`~/Library/LaunchAgents/com.kazuki-macmini.discord-bot-*.plist` が GUI domain にロードされていないことを確認した
- login keychain を解除し、Discord bot token / Codex CLI の起動経路を確認した
- `general` / `kb` / `kanojo` / `yumekano-coe` を `nohup .venv/bin/python -u bot.py <bot>` で手動復旧した
- 各 bot の Discord Gateway 接続ログを確認した
- Tailscale 側の `ssh macmini` 接続復旧も確認した

## Discoveries
- Tailscale の `macmini` は当初 SSH 22番がタイムアウトし、LAN の `macmini-lan` は応答したが `This system is locked` で鍵認証できなかった
- `who` は空で、`launchctl print gui/501` は `Domain does not support specified action` を返したため、GUI セッションが存在しない状態だった
- bot ログは 2026-05-30 03:02 JST 付近で止まっており、その後の再起動で LaunchAgent が復帰していなかった
- `launchctl bootstrap user/501` は `Bootstrap failed: 5: Input/output error` で失敗した。既存 plist は GUI LaunchAgent 前提で、background user domain にはそのまま載せられなかった
- `/Applications/Codex.app/Contents/Resources/codex` は存在し、短い `codex exec` は応答した。OAuth refresh / websocket 周りのネットワーク警告は出たが、応答自体は返った

## Decisions
- 恒久設定は変更せず、保存済み Mac mini パスワードで SSH ログイン・keychain unlock を行った上で、手動プロセス起動に留めた
- 理由: GUI 未ログイン状態では LaunchAgent domain が使えず、今すぐ Discord bot を復旧するには手動起動が最小対応だったため

## Notes
- 一時復旧 PID: `general=3410`, `kb=3412`, `kanojo=3414`, `yumekano-coe=3416`
- 次回再起動時は同じ問題が再発する可能性がある
- 恒久対策候補: Mac mini に GUI ログインする、または bot を keychain / GUI session 非依存の launchd service へ移行する
