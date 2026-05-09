# エポスカード利用サマリーBot (kanojo) 実装

## Goal
- 1日3回 (7:00 / 15:00 / 22:00 JST) にエポスカード利用状況を Discord に投稿する
- 投稿スレッドへ直接返信したら Claude Code が文脈を保ったまま応答する

## Done

### コード実装 (Tasks 1-17, ローカル + macmini で 68 テスト全 pass)
- `card_summary/` パッケージ新規作成 (7 モジュール)
  - `store.py` SQLite 永続化 (transactions / category_rules / monthly_close / summary_state / fetch_checkpoint)
  - `parser.py` エポス通知メール → Transaction
  - `categorizer.py` 加盟店 → カテゴリ (辞書 + LLM フォールバック)
  - `analyzer.py` 月累計 / カテゴリ別 / 異常検知 / SummaryReport / has_changed (4値ハッシュ)
  - `formatter.py` Discord 投稿テキスト生成
  - `gmail_fetcher.py` Gmail API 直接呼び出し (OAuth2)
  - `scheduler.py` `run_slot()` + `start_scheduler()` (asyncio loop)
- `bot.py` 改修2点
  - `on_ready` で kanojo bot のときのみ scheduler を `asyncio.create_task` で起動
  - `handle_thread_message` で初回 Claude 呼び出し時に `kanojo_context_file` をプロンプトに `<background>` として注入
- `config.json` に kanojo bot エントリ追加 (dir=`~/kanojo`, control_channel_id=1497151379393876020)
- `pytest.ini` に asyncio_mode = auto

### macmini 統合
- `macmini/wip-kanojo-prep` ブランチで macmini 側の WIP 作業 (claude_errors / discord_test_auth / silent-exit retry) を保護してから feat ブランチへマージ
- main マージ + push (`f983ab2`) 完了
- LaunchAgent 4 bot (general / kb / kanojo / yumekano-coe) 全部 plist 再生成 + bootstrap 完了
- kanojo bot プロセス起動確認 (PID 50126)、scheduler 起動ログ確認

### Token と OAuth
- kanojo Discord bot token を `~/discord-bots/.env` に設定 (keychain は SSH からだと user-interaction-required で書き込めず .env で代替)
- Gmail OAuth credentials (Desktop OAuth Client) を `data/gmail_credentials.json` に配置
- 認証フロー実行 → `data/gmail_token.json` 取得、有効
- 実 Gmail で 10 件のエポス通知メール parser 通過 (新フォーマット対応後)

### parser フォーマット修正 (`5e7457a`)
- 実メールが `【】` 全角ブラケットではなく `ご利用日時：` 全角コロン形式と判明
- merchant フィールドは「国内加盟店ショッピング」等の汎用文言のみ (具体加盟店名なし)
- 正規表現を新フォーマットに対応、フィクスチャも実メール準拠に redact 済み

### エポスNet scheduler 統合 (`2026-05-09`)
- macmini に Playwright 1.59.0 + Chromium をインストール
- `card_summary/scheduler.py` に 03:00 JST の daily reconciliation loop を追加
  - 既存の 7:00 / 15:00 / 22:00 投稿 slot loop は維持
  - `run_reconciliation()` が当月 + 前月の 2 ヶ月分を `epos_scraper.fetch_month_history()` で取得
  - `Categorizer` でカテゴリ付与後、`store.upsert_transactions()` に投入
  - 失敗時は exception log のみで loop 継続
- `tests/card_summary/test_scheduler_recon.py` を追加
  - `_next_recon_run()` の 03:00 計算
  - mocked fetcher が当月 + 前月で呼ばれ、`AP/サミット` が `食費` として DB に入ることを確認
- macmini 実行結果:
  - `pytest tests/card_summary/ -q` → 55 passed
  - `pytest -q` → 81 passed

## Discoveries

### 重要な前提崩壊
- **メール本文に加盟店名が含まれない**
  実メールは `ご利用場所：国内加盟店ショッピング` のような汎用文言のみで、Amazon やセブン-イレブンといった具体的店名は来ない。
  spec § 8 のカテゴリ分類辞書 (AMAZON→ネット通販 等) は実質機能しない。
- **エポスメール通知は 2023/11 で停止していた**
  「エポスアプリ通知 ON にするとメール通知が自動 OFF」という仕様。
  かーくんがアプリ通知を OFF にしてメール通知を再開してくれた。
- **エポスNet なら加盟店名込みで全部取れる**
  `/memberservice/pc/usehistoryreference/use_history_preload.do` 月別ご利用履歴照会
  に「ご利用年月日 / ご利用場所 (実加盟店名) / ご利用金額 / 支払区分 / お支払開始月」が
  完全な明細として表示される。「データのダウンロード」タブもあり CSV 取得の可能性。

### Discord application 構造
- `kanojo` という名前の Discord application は存在しない
- 実体は **「reserved-bot」application (ID `1493201132510511214`) の Bot user 名が "kanojo#3813"**
- application 名は reserved-bot のままだが、bot user 表示名は kanojo
- macmini 側 WIP の「reserved → kanojo 置換」は config.json 上の bot 名のみで、Discord application は同じ

### macmini SSH 経由の制約
- `security add-generic-password` は SSH (非 GUI) セッションだと `User interaction is not allowed` で失敗
- 既存 `bot.py` の `get_from_env_file` を使い `~/discord-bots/.env` への書き込みで回避
- LaunchAgent (gui/$UID) は keychain アクセス可能 (memory にも記載済み)
- 2026-05-09 追記: 現在の macmini login keychain では `service=epos-net` も `account=epos-email|epos-pass|epos-cvv` も未検出。CLI と GUI Terminal `.command` 実行の両方で `SecKeychainSearchCopyNext: The specified item could not be found`。selector live DOM 検証は credential 再登録後に再試行する

### Discord Developer Portal / Google Cloud / エポスNet の browser-use 操作
- browser-use 独自プロファイルは Chrome の普段ログインを使えない (= かーくん側ブラウザと「タブ競合」が起きやすい)
- Discord token reset は本人が普段 Chrome で進める方が安全 (clipboard → ssh stdin で macmini 登録)
- Gmail OAuth は Test users に kazu312stone@gmail.com を追加し忘れて access_denied (新 UI では「対象」タブ)
- エポスNet ログインは ID/Pass/CVV 全部 keychain (`epos-email` / `epos-pass` / `epos-cvv`, service=`epos-net`) に登録済みだが、今回は CVV プロンプトが出ずスキップされた

## Decisions

- **2026-05-08**: Discord token は `~/discord-bots/.env` で管理 (keychain は LaunchAgent 起動後に手動移行が望ましい)
- **2026-05-08**: kanojo bot の `dir` は `~/kanojo`、`control_channel_id` は `1497151379393876020` (macmini WIP 採用)
- **2026-05-08**: `bot.py` の改修は spec § 10 の mention 緩和ではなく context-file 注入のみ。既存 `is_thread + is_our_channel` 経路で十分
- **2026-05-09**: 設計を **エポスNet スクレイピング中心** にピボット。メール parser は legacy で残すが、新規実装は `card_summary/epos_scraper.py` (Playwright + keychain) を主機能とする

## Next Steps (PLANS.md に移行)

1. `card_summary/epos_scraper.py` 新規実装 (Playwright + keychain ID/Pass/CVV)
2. cookie 永続化 (CVV プロンプト回避)
3. `card_summary/scheduler.py` を Gmail fetch から エポスNet scrape に差し替え (03:00 reconciliation loop として完了。Gmail slot fetch は legacy/併用として維持)
4. `CATEGORY_SEED` を実加盟店向けに更新 (GOOGLE→サブスク、UBER→食費、SUMITOMO/サミット→食費 等)
5. macmini に Playwright + chromium インストール (完了)
6. データダウンロード CSV 取得が可能か検証 (HTML scraping より安定)
7. (optional) keychain への kanojo token 移行 (.env から)

## Notes

- 月別ご利用履歴照会の URL: `https://www.eposcard.co.jp/memberservice/pc/usehistoryreference/use_history_preload.do`
- お支払予定額照会の URL: `https://www.eposcard.co.jp/memberservice/pc/paymentamountreference/payment_reference_preload.do`
- ログイン URL: `https://www.eposcard.co.jp/memberservice/pc/login/login_preload.do`
- ログアウト URL: `/memberservice/pc/logout.do`
- 過去 12 ヵ月履歴は `/memberservice/pc/paymenthistoryreference/paymenthistoryreference_preload.do`
- spec: `docs/superpowers/specs/2026-05-07-card-summary-bot-design.md`
- plan: `docs/superpowers/plans/2026-05-07-card-summary-bot-plan.md`
