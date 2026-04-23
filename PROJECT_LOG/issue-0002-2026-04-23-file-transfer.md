# Discord ファイル送受信（_inbox / _outbox 規約）

## Goal
- ユーザー → bot: 画像 / PDF などの添付を受け取って Claude に渡す
- bot → ユーザー: Claude が生成したファイル（PDF, 画像, CSV など）を Discord に返送

## Done
- `attachments.py` 新設: format_inbox_for_prompt / filter_sendable / chunk_for_messages（8 tests green）
- `bot.py` に以下を追加:
  - `save_inbox_attachments`: `message.attachments` を worktree/`_inbox/` に `attachment.save()` で保存
  - `send_outbox_files`: Claude 実行後 `_outbox/` を再帰走査、10MB 以下は `discord.File` で batch（≤10件/msg）送信、送信後削除
  - `build_prompt_with_inbox`: プロンプト冒頭に `_inbox` の listing と `_outbox` への出力ルールを毎回添付
- `handle_new_session` / `handle_forum_new_session` / `handle_thread_message` 全てで同じ流れを実装
- E2E: Discord で画像添付→「180度回転したpngにして」依頼→回転済み画像が bot から返送（ログに `+1files`, cost $0.1716 + $0.2055）

## Discoveries
- Discord の1ファイル上限は **10MiB**（bot/Free）。Server Boost でも上がらない（公式仕様が変わっていた）
- 1メッセージあたり **10ファイルまで**
- `discord.File` は single-use で同じインスタンスを再利用できない（batch ごとに新規生成）
- `attachment.save(target)` は aiohttp ダウンロード + バイト書き込みを内部で行う。存在しないディレクトリは事前に `mkdir` しておく必要あり
- `_outbox/` 配信後の削除は `unlink()` で十分だがディレクトリ自体は残す（次ターンで空スキャンできる）

## Decisions
- 案A（最小実装）採用。10MB 超が頻発したら案B (Drive 連携) に移行する前提でロジックを隔離（attachments.py）
- prompt 冒頭に `[Discord 連携ルール]` プリアンブルを**毎ターン**含める。セッション頭だけだと Claude が長い会話中に忘れる懸念があり、トークン効率より UX の安定性を優先
- `_outbox/` は rglob で再帰検出。Claude がサブディレクトリに書いても回収できる

## Notes
- `_inbox/` はセッション終了時に worktree 毎破棄されるのでクリーンアップ不要
- 画像 roundtrip で確認済。PDF / CSV など他形式も discord.File 経由で同じフローで送れるはず
- 10MB 超の時は Discord に `⚠️ xxx is too large (N.N MB > 10 MB limit)` テキストだけ投稿する
