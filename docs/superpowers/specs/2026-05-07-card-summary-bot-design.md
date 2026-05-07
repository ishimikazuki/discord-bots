# エポスカード利用サマリーBot 設計書

**作成日:** 2026-05-07
**対象:** `~/discord-bots/` (kanojo bot)
**ゴール:** エポスカードの利用履歴を1日3回 Discord に要約投稿し、その投稿スレッドでの返信に Claude Code が文脈を保ったまま応答するBotを構築する。

---

## 1. 背景と目的

### 課題
- Pythonなどシステマチックに Discord に投稿された通知に対してスレッドで返信しても、Claude Code セッションが紐付いていないため反応しない／新規セッションが立ち上がって文脈を失う。
- クレジットカード利用状況を能動的に追うのは面倒で、月末に予算オーバーに気づくことが多い。

### 解決
- Bot 自身が起点のサマリースレッドを作成して投稿し、スレッド内では mention 不要で Claude Code が応答する仕組みを既存 `bot.py` に追加する。
- `~/discord-bots/` の既存アーキテクチャ（メンション → スレッド → `sessions.json` で `thread_id ↔ session_id` 永続化）を最大限活用する。

---

## 2. 要件

### 機能要件
1. **取得元**
   - 主: Gmail MCP 経由でエポスカードの「カードご利用のお知らせ」メールを取得（リアルタイム性◎、認証問題なし）
   - 補: 月1回 browser-use でエポスNet にログインし確定額を取得・自前 DB と突合
2. **タイミング:** 7:00 / 15:00 / 22:00 (JST) 1日3回
3. **サマリー内容**
   - 今月累計（前月同日比 +金額・%付き）
   - カテゴリ別内訳（食費・交通・サブスク・コンビニ・ネット通販・その他）
   - 大きい支出ハイライト（前回投稿以降の最大支出）
   - 異常検知アラート（後述の閾値ロジック）
4. **静音化:** 前回投稿時から `(累計, カテゴリ別breakdownハッシュ, 最大tx_id, アラートhash)` がすべて変化していない場合は何も投稿しない
5. **返信応答:** Bot が立てたサマリースレッド内のメッセージは mention 不要で Claude Code セッションが応答。`sessions.json` の `thread_id ↔ session_id` 機構を流用。

### 非機能要件
- 取得・パースは冪等（同じメールを2回処理しても重複保存しない）
- Bot プロセス再起動後も状態を失わない（SQLite 永続化）
- 1日3回バッチの所要時間は通常 30秒以内

### スコープ外（YAGNI）
- 複数カード対応（エポスのみ）
- カード会社サイトでの自動引き落とし金額管理
- 予算設定機能・支出制限機能
- 多通貨対応
- Web ダッシュボード

---

## 3. アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│  ~/discord-bots/  (LaunchAgent 常駐の単一プロセス)             │
│                                                          │
│  ┌────────────────┐     ┌─────────────────────────────┐  │
│  │  bot.py        │     │  card_summary/  (新規モジュール) │  │
│  │  (改修2箇所のみ)  │◀───▶│                              │  │
│  │                │     │  gmail_fetcher / parser       │  │
│  │  ① schedulerを  │     │  categorizer  / store         │  │
│  │     起動時loop登録 │     │  analyzer     / formatter     │  │
│  │  ② 自スレッド内は  │     │  scheduler    / reconciler    │  │
│  │     mention不要   │     │  config                      │  │
│  └────────────────┘     └─────────────────────────────┘  │
│         │                            │                   │
│         ▼                            ▼                   │
│  Discord (kanojo bot)         data/card.sqlite3          │
└──────────────────────────────────────────────────────────┘
```

### 設計原則
- **既存規約を流用する:** `thread_id = Claude Code session の単位` という既存規約をそのまま使う
- **モジュール境界を明確に:** `card_summary/` は bot.py からは「scheduler.run_slot(slot)」一つで呼べるインターフェースに集約
- **副作用は store/discord 投稿に局所化:** parser/analyzer/formatter はすべて純関数

---

## 4. ファイル構造

```
~/discord-bots/
├── bot.py                          # 改修 (2箇所)
├── card_summary/                   # 新規ディレクトリ
│   ├── __init__.py
│   ├── config.py                   # チャンネルID、閾値、カテゴリ辞書
│   ├── gmail_fetcher.py            # Gmail MCP で「ご利用のお知らせ」取得
│   ├── parser.py                   # メール本文 → 構造化データ
│   ├── categorizer.py              # 加盟店 → カテゴリ (辞書 + LLM フォールバック)
│   ├── store.py                    # SQLite 永続化レイヤ
│   ├── analyzer.py                 # 累計・前月比・異常検知・変化判定
│   ├── formatter.py                # Discord メッセージ生成
│   ├── scheduler.py                # 7/15/22時 の asyncio タスク + run_slot()
│   └── reconciler.py               # 月1回 browser-use で確定額補正
├── data/
│   └── card.sqlite3                # 新規 DB
├── docs/superpowers/
│   ├── specs/2026-05-07-card-summary-bot-design.md  # 本ドキュメント
│   └── plans/2026-05-07-card-summary-bot-plan.md    # writing-plans で生成
└── tests/card_summary/
    ├── __init__.py
    ├── test_parser.py
    ├── test_categorizer.py
    ├── test_store.py
    ├── test_analyzer.py
    ├── test_formatter.py
    ├── test_scheduler.py
    ├── test_reconciler.py
    └── fixtures/
        └── epos_email_*.txt        # 実メールのサンプル (PII redact 済)
```

---

## 5. データフロー

### 5a. 定時バッチ（7:00 / 15:00 / 22:00）

slot 名は `7:00 → 'morning'`, `15:00 → 'afternoon'`, `22:00 → 'night'` の3値で固定（DB CHECK 制約と完全一致）。

```
scheduler.run_slot(slot)  # slot は 'morning' | 'afternoon' | 'night' のいずれか
  │
  ▼
gmail_fetcher.fetch_new_since(last_fetch_at)
  │  Gmail MCP search: from:notice@eposcard.co.jp newer_than:7d
  │  message_id で重複排除
  ▼
parser.parse(email_body) → [Transaction(occurred_at, merchant, amount, source_id)]
  │
  ▼
categorizer.categorize(merchant) → category
  │  辞書ヒット → 即返却
  │  辞書ミス → LLM フォールバック (Claude Haiku) → category_rules に学習保存
  ▼
store.upsert_transactions(transactions)  # source_id UNIQUE で冪等
store.update_fetch_checkpoint(now)
  │
  ▼
analyzer.compute(slot) → SummaryReport
  │  ・month_total
  │  ・vs_prev_month_same_day (絶対額・%)
  │  ・category_breakdown (Dict[category, amount])
  │  ・highlight_tx (前回投稿以降の最大支出)
  │  ・alerts: List[Alert]
  ▼
analyzer.has_changed(slot, report) ?
  │
  ├─ NO  → ログのみ出力して終了 (静音化)
  │
  └─ YES → formatter.format(report) → str
            │
            ▼
        bot がエポスフォーラムにスレッド作成 + 投稿
            │  thread name: "🔔 {date} {slot}"
            │  thread auto_archive: 1 week
            ▼
        sessions.json[thread_id] = {projectKey: 'kanojo', sessionId: null, ...}
        store.update_summary_state(slot, report, thread_id)
```

### 5b. かーくんの返信（既存スレッド内に書き込むだけ）
```
on_message イベント
  │
  ▼
[改修箇所] スレッド種別判定:
  既存ロジック: mention → スレッド作成
  追加: 自スレッド (sessions.json に登録済 + projectKey=='kanojo')
        → mention 不要で応答経路へ
  │
  ▼
sessions.json から session_id 取得 (なければ新規作成)
  │
  ▼
claude --resume {session_id} で応答
  │
  ▼
sessions.json[thread_id].sessionId, lastUsed を更新
```

### 5c. 月1回の補正（毎月1日 03:00）
```
reconciler.run() が起動 (scheduler 内の月次トリガで)
  │
  ▼
browser-use でエポスNet ログイン (token を keychain から取得)
  │  MFA 必要時はエラーch通知してスキップ
  ▼
前月の確定額・利用履歴をダウンロード
  │
  ▼
store.reconcile(year_month, confirmed_amount, transactions)
  │  ・monthly_close を upsert
  │  ・自前DBに無い tx を INSERT (source='epos_net')
  │  ・差額 > ¥1,000 ならエラーch 通知
```

---

## 6. データモデル (SQLite)

```sql
-- 利用履歴
CREATE TABLE transactions (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,          -- ISO8601 (JST)
  merchant TEXT NOT NULL,
  amount INTEGER NOT NULL,            -- 円 (整数)
  category TEXT,                      -- nullable, 後追い分類可能
  source TEXT NOT NULL CHECK (source IN ('gmail', 'epos_net')),
  source_id TEXT NOT NULL UNIQUE,     -- Gmail message_id or epos tx_id
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_transactions_occurred_at ON transactions(occurred_at);

-- カテゴリ辞書 (加盟店パターン → カテゴリ)
CREATE TABLE category_rules (
  pattern TEXT PRIMARY KEY,           -- 大文字に正規化, 例: 'AMAZON'
  category TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('seed', 'llm', 'manual')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 月次確定額
CREATE TABLE monthly_close (
  year_month TEXT PRIMARY KEY,        -- 'YYYY-MM'
  confirmed_amount INTEGER NOT NULL,
  fetched_at TEXT NOT NULL
);

-- 「変化なしスキップ」判定用
CREATE TABLE summary_state (
  slot TEXT PRIMARY KEY CHECK (slot IN ('morning', 'afternoon', 'night')),
  last_posted_at TEXT,
  last_total INTEGER,
  last_breakdown_hash TEXT,           -- カテゴリbreakdown を JSON sorted-stringify した SHA256
  last_max_tx_id INTEGER,
  last_alert_hash TEXT,               -- alert list を SHA256
  last_thread_id TEXT
);

-- Gmail 取り込みのチェックポイント
CREATE TABLE fetch_checkpoint (
  source TEXT PRIMARY KEY,            -- 'gmail'
  last_fetch_at TEXT NOT NULL
);
```

### 「変化なし」判定の詳細
`(last_total, last_breakdown_hash, last_max_tx_id, last_alert_hash)` の4値すべてが新算出値と一致したらスキップ。1つでも違えば投稿。

---

## 7. 異常検知ロジック

| アラート | 条件 |
|---|---|
| **月ペース急増** | (今月累計) ÷ (今月経過日数) × 30 > (前月累計) × 1.3 |
| **カテゴリ急増** | カテゴリ別 (今月累計) > (前月同日カテゴリ累計) × 2.0 |
| **単発高額** | 単一 tx の amount が 過去30日中央値 × 5 以上 |

すべて閾値は `card_summary/config.py` 定数で外出し（後でチューニングしやすく）。

---

## 8. カテゴリ分類

### 初期辞書（`config.py` に seed として埋め込む）
```python
CATEGORY_SEED = {
    "AMAZON":           "ネット通販",
    "RAKUTEN":          "ネット通販",
    "SEVEN-ELEVEN":     "コンビニ",
    "FAMILYMART":       "コンビニ",
    "LAWSON":           "コンビニ",
    "SUICA":            "交通",
    "JR EAST":          "交通",
    "NETFLIX":          "サブスク",
    "SPOTIFY":          "サブスク",
    "OPENAI":           "サブスク",
    "ANTHROPIC":        "サブスク",
    # ... 拡張は LLM が learn する
}
```

### LLM フォールバック
- 辞書ミス時、Claude Haiku を呼び出し以下のプロンプトで分類:
  ```
  以下の加盟店を [食費 / 交通 / サブスク / コンビニ / ネット通販 / 衣料 / 医療 / その他] のいずれかに分類してください。
  加盟店: {merchant}
  カテゴリのみ1単語で答えてください。
  ```
- 結果を `category_rules` に `source='llm'` で保存。次回以降は辞書ヒット。
- LLM 呼び出し失敗時は `category=null` で保存し、次バッチで再試行。

---

## 9. 投稿フォーマット

```
🔔 7:00 サマリー (5/7)
─────────────────────
今月累計: ¥48,200
　前月同日比: +¥3,500 (+7.8%)

📊 カテゴリ別:
  食費        ¥18,500
  サブスク     ¥ 8,000
  コンビニ    ¥ 5,200
  ネット通販  ¥10,500
  その他      ¥ 6,000

🏆 ハイライト:
  Amazon ¥3,200 (昨夜 23:42)

⚠️ アラート:
  食費が前月同日比 +120% (今月ペース注意!)
  月ペース予測: ¥72,300 (前月 ¥58,000)
```

「変化ゼロ」時は **完全沈黙**（メッセージ送信なし、デバッグログのみ）。

---

## 10. bot.py 改修ポイント

### 改修1: scheduler 起動
```python
# bot.py の on_ready 内 (kanojo bot のみ)
if self.bot_key == "kanojo":
    from card_summary.scheduler import start_scheduler
    asyncio.create_task(start_scheduler(self))
```

### 改修2: kanojo bot 配下のスレッドは mention 不要
```python
# bot.py の on_message 内
# kanojo bot の場合、自分のフォーラム配下のスレッドは mention 不要で応答する
is_kanojo_thread = (
    self.bot_key == "kanojo"
    and isinstance(message.channel, discord.Thread)
    and message.channel.parent_id == KANOJO_FORUM_CHANNEL_ID
)
if is_kanojo_thread:
    # mention check をスキップして応答経路へ
    # sessions.json に thread_id が無ければ新規 session を発行
    pass
```

判定基準は **「kanojo bot の専用フォーラム配下のスレッドかどうか」** のみ。`sessions.json` 登録の有無で判定すると、システマチック投稿で作ったスレッドと人間がメンションして作ったスレッドの区別が必要になり複雑化するため、フォーラム単位で許可するシンプルな方針を採用。

`KANOJO_FORUM_CHANNEL_ID` は `card_summary/config.py` で定数管理。

---

## 11. エラーハンドリング

| エラー | 対応 |
|---|---|
| Gmail MCP 取得失敗 | 1回リトライ (10秒待機) → 失敗時はログのみ、当該スロット投稿スキップ |
| パース失敗 (未知形式) | エラーチャンネルに本文付きで通知、当該tx は保存スキップ |
| LLM 分類失敗 | `category=null` で保存、次バッチで再分類 |
| browser-use 補正失敗 | 翌日同時刻にリトライ、3回失敗でエラーch通知 |
| SQLite ロック | exponential backoff で最大3回 retry |
| Discord 投稿失敗 | `summary_state` を更新せず、次バッチで再投稿 |
| `kanojo-bot-token` 未取得 | LaunchAgent 起動時に keychain アクセス失敗 → 既存 bot 群と同じく LaunchAgent ログに記録 |

---

## 12. テスト方針

### ユニットテスト
| モジュール | カバー範囲 |
|---|---|
| parser | エポスメール本文の各バリエーション (通常・キャンセル・海外利用・分割) |
| categorizer | 辞書ヒット / LLM フォールバック (LLM はモック) / 学習保存 |
| store | upsert 冪等性、source_id UNIQUE 違反、変化判定 |
| analyzer | 月累計・前月比・カテゴリ集計・3種のアラート閾値境界 |
| formatter | フル投稿・変化ゼロ判定・空カテゴリの扱い |
| scheduler | freezegun で 7:00/15:00/22:00 トリガ、変化ゼロ時の沈黙 |
| reconciler | browser-use モック、差額検知 |

### 統合テスト
- フィクスチャメール群 → DB → analyzer → formatter までを一気通貫で実行し、出力文字列を snapshot 比較

### 冪等性テスト
- 同じメール群を2回流しても `transactions.count` が変わらないこと
- 同じスロットを2回連続実行すると2回目は沈黙すること

---

## 13. デプロイ・運用

### 配置
- 既存 `~/discord-bots/` リポジトリにマージ（同一 LaunchAgent で動く）
- `requirements.txt` に新規依存を追加（後述）
- macmini 上で `bash launchd/install-macmini.sh` を再実行して plist 更新

### 新規依存
- `google-api-python-client`, `google-auth`, `google-auth-oauthlib` (Gmail API 直叩き用)
- `freezegun` (テスト用、時刻固定)

### Gmail 取得方法 (確定)
`mcp__claude_ai_Gmail__*` MCP ツールは Claude Code セッション内でしか叩けないため、bot プロセスからは **Google Gmail API を直接呼び出す**。
- スコープ: `https://www.googleapis.com/auth/gmail.readonly`
- 認証: 初回 OAuth2 で refresh_token を取得し `data/gmail_token.json` に保存（`.gitignore` 追加）
- 検索クエリ: `from:eposcard@eposcard.co.jp newer_than:7d` を `users.messages.list` で取得 → `users.messages.get` で本文取得
- アクセスは `gmail_fetcher.py` 内に局所化、他モジュールは構造化 Transaction だけを受け取る

### 設定
- `~/discord-bots/config.json` に `kanojo` エントリを追加（dir: `~/discord-bots`, control_channel_id: 新規, emoji: 💳）
- keychain に `kanojo-bot-token` を登録（実装直前に対応）
- エポスフォーラム channel_id を `card_summary/config.py` に設定

### モニタリング
- ログファイル: `~/discord-bots/logs/kanojo.log`（既存パターン踏襲）
- 異常時はエラーチャンネル（`config.json` の `notify_channel_id`）に投稿

---

## 14. 実装順序の方針 (writing-plans への引き継ぎ)

1. SQLite スキーマと store モジュール
2. parser (フィクスチャ駆動TDD)
3. categorizer (辞書 + LLM モック)
4. analyzer (累計・比較・アラート)
5. formatter
6. gmail_fetcher (Gmail API 直叩き、上記 案2)
7. scheduler (asyncio + freezegun テスト)
8. bot.py 改修2点 (scheduler 起動 + mention 緩和)
9. config.json に kanojo bot 追加 + plist 再生成
10. reconciler (browser-use, 後回し可)
11. 本番投入 + 1週間モニタ

各タスクは TDD（Red → Green → Refactor → Commit）。

---

## 15. 確認事項チェックリスト

- [x] Bot 名: `kanojo`
- [x] 取得元: Gmail (主) + browser-use (月次補正)
- [x] 時刻: 7:00 / 15:00 / 22:00
- [x] サマリー内容: 累計 + カテゴリ + ハイライト + 異常検知
- [x] 静音化: 4値ハッシュ一致なら投稿スキップ
- [x] 返信応答: 自スレッド内は mention 不要、`sessions.json` で session 継続
- [x] スコープ: エポスのみ、予算機能なし、Web UIなし
