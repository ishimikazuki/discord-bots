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
- [ ] selector を実 DOM で verify (`headless=False` + 月選択挙動)
- [x] `card_summary/scheduler.py` に epos_scraper 統合 (1日1回 深夜 3:00 呼び出し)
- [ ] (optional) keychain へ kanojo bot token 移行 (.env から)

## 発見・予想外のこと
- 2026-05-08: 実メール本文に加盟店名がない（「ご利用場所：国内加盟店ショッピング」のみ）
- 2026-05-08: エポスメール通知は 2023/11 で停止していた（アプリ通知 ON で自動 OFF 仕様）
- 2026-05-08: kanojo Discord application は存在しない、実体は reserved-bot application の bot user 名 = kanojo#3813
- 2026-05-08: SSH 経由の `security add-generic-password` は user-interaction-required で失敗、`.env` で代替
- 2026-05-09: エポスNet 月別ご利用履歴照会で加盟店名・金額・日付の完全明細取得可能と判明、設計ピボット決定
- 2026-05-09: macmini 現在ユーザーの login keychain には `service=epos-net` / `account=epos-email|epos-pass|epos-cvv` が見つからない。GUI Terminal 経由でも `SecKeychainSearchCopyNext: The specified item could not be found`。live DOM 検証は credential 再登録後に再実行が必要

## 決定したこと
- 2026-05-07: spec § 10 の bot.py mention 緩和は不要、既存 `is_thread + is_our_channel` 経路 + context-file 注入で要件達成
- 2026-05-08: kanojo bot の dir は `~/kanojo`、control_channel_id は `1497151379393876020`
- 2026-05-09: メール parser は legacy として残すが、新規データ取得は エポスNet スクレイピング中心に切り替え

## メモ
- spec: `docs/superpowers/specs/2026-05-07-card-summary-bot-design.md`
- plan: `docs/superpowers/plans/2026-05-07-card-summary-bot-plan.md`
- 完了タスク詳細: `PROJECT_LOG/issue-0003-2026-05-07-card-summary-bot.md`
- エポスNet 月別ご利用履歴照会 URL: `/memberservice/pc/usehistoryreference/use_history_preload.do`
- keychain 期待値: `epos-email` / `epos-pass` / `epos-cvv` (service=`epos-net`)。2026-05-09 時点の macmini login keychain では未検出
- `kanojo-bot-token` は macmini の .env のみ
