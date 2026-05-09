# 現在の作業: card_summary をエポスNet スクレイピング中心に切替

## ゴール
- カード利用履歴を Discord に 1日3回サマリー投稿（加盟店名・カテゴリ別含む）
- 投稿スレッド内で kanojo bot に質問できる

## 進捗 (常に最新に更新)
- [x] card_summary パッケージ実装 (Tasks 1-17, 全 68 テスト pass)
- [x] kanojo bot LaunchAgent 起動 (PID 50126 確認済み)
- [x] Gmail OAuth セットアップ完了 (token 取得 + 実メール 10 件 parser 通過)
- [x] エポスNet ログイン手動検証成功 (CVV 不要だった)
- [x] 月別ご利用履歴照会で加盟店名込みの明細取得を確認
- [x] 設計ピボット: メール → エポスNet スクレイピング中心
- [x] `card_summary/epos_scraper.py` 新規実装 (Playwright + keychain + cookie 永続化)
- [x] CATEGORY_SEED を実加盟店向けに更新
- [x] `get_category_for` を LENGTH 降順 lookup に修正 (具体的 pattern が generic を上書き)
- [x] `tests/card_summary/test_epos_scraper.py` ユニット 10 件追加 (78 pass)
- [x] macmini で `pip install -r requirements.txt && playwright install chromium`
- [x] selector を実 DOM で verify (`headless=False` + 月選択挙動)
- [x] Playwright が Epos の信頼判定で弾かれる場合、信頼済み Chrome + AppleScript で月別履歴を取得するフォールバックを追加
- [x] Epos `source_id` を表全体の行番号依存から同一明細内の出現回数依存に修正し、途中に明細が増えても既存行が重複しないようにした
- [x] `card_summary/scheduler.py` に epos_scraper 統合 (1日1回 深夜 3:00 呼び出し)
- [x] Discord Bot のAI実行基盤を Claude Code から Codex CLI に置換
- [x] Codex移行後のカード要約・予定調整・過去スレッド再現契約テストを追加（Discord Bot 98 pass / kanojo 91 pass）
- [ ] (optional) keychain へ kanojo bot token 移行 (.env から)

## 発見・予想外のこと
- 2026-05-08: 実メール本文に加盟店名がない（「ご利用場所：国内加盟店ショッピング」のみ）
- 2026-05-08: エポスメール通知は 2023/11 で停止していた（アプリ通知 ON で自動 OFF 仕様）
- 2026-05-08: kanojo Discord application は存在しない、実体は reserved-bot application の bot user 名 = kanojo#3813
- 2026-05-08: SSH 経由の `security add-generic-password` は user-interaction-required で失敗、`.env` で代替
- 2026-05-09: エポスNet 月別ご利用履歴照会で加盟店名・金額・日付の完全明細取得可能と判明、設計ピボット決定
- 2026-05-09: macmini 現在ユーザーの login keychain には `service=epos-net` / `account=epos-email|epos-pass|epos-cvv` が見つからない。GUI Terminal 経由でも `SecKeychainSearchCopyNext: The specified item could not be found`。live DOM 検証は credential 再登録後に再実行が必要
- 2026-05-09: keychain 再登録後、credentials 3 件は取得 OK。既存 Google Chrome profile + Codex Chrome Extension ではログイン/CVV/月別履歴 DOM 取得に成功し、2026年5月のショッピング明細 17 件・合計 41,815 円を確認。一方、Python Playwright の新規 context は画像認証（パズル）でブロックされるため、`fetch_month_history()` の headless 自動運用は追加対策が必要
- 2026-05-09: Chrome の `browser.allow_javascript_apple_events` を有効化し、`fetch_month_history()` が Playwright challenge 時に Google Chrome の信頼済みプロファイルを AppleScript 経由で使うフォールバックを追加。実 Epos で 2026年5月 19 件 / 49,535 円、2026年4月 81 件 / 290,987 円を取得し、DB 反映後の再実行は `inserted=0` で重複なし
- 2026-05-09: 旧 `source_id` が表全体の行番号に依存しており、新しい明細が途中に入ると既存行まで別 ID になって重複登録されることを発見。発生済み重複 2 件を削除し、DB 内 100 件を安定 ID へ rekey 済み
- 2026-05-09: Codex CLI (`codex exec --json`) は新規 thread と resume の両方で JSONL イベントを返すことを確認済み。stderr に plugin/skill manifest 警告が出るが、exit 0 なら Bot 応答には影響しない
- 2026-05-09: `~/kanojo` の project-local skill `screenshot-to-calendar` は `.agents/skills -> ../.claude/skills` を commit/push 済み。Codex `debug prompt-input` で利用可能 skill として表示されることを確認

## 決定したこと
- 2026-05-07: spec § 10 の bot.py mention 緩和は不要、既存 `is_thread + is_our_channel` 経路 + context-file 注入で要件達成
- 2026-05-08: kanojo bot の dir は `~/kanojo`、control_channel_id は `1497151379393876020`
- 2026-05-09: メール parser は legacy として残すが、新規データ取得は エポスNet スクレイピング中心に切り替え
- 2026-05-09: Discord Bot の対話エージェントは Codex に統一する。旧 Claude sessionId は Codex で再開できないため、旧スレッドでは新規スレッド開始を案内する
- 2026-05-09: Epos Net の自動取得は Playwright を第一候補にしつつ、challenge / session rejection 時は macmini の信頼済み Google Chrome profile を AppleScript で操作して取得する。Chrome 側では `browser.allow_javascript_apple_events=true` が必要

## メモ
- spec: `docs/superpowers/specs/2026-05-07-card-summary-bot-design.md`
- plan: `docs/superpowers/plans/2026-05-07-card-summary-bot-plan.md`
- 完了タスク詳細: `PROJECT_LOG/issue-0003-2026-05-07-card-summary-bot.md`
- エポスNet 月別ご利用履歴照会 URL: `/memberservice/pc/usehistoryreference/use_history_preload.do`
- keychain 期待値: `epos-email` / `epos-pass` / `epos-cvv` (service=`epos-net`)。2026-05-09 現在は macmini login keychain から取得 OK
- `kanojo-bot-token` は macmini の .env のみ
