# Discord Bots

Python版のDiscord bot群です。1 bot = 1 projectとして起動し、Discordの投稿からCodex CLIを呼び出して、スレッド単位で会話を継続します。

このリポジトリは友人へ共有しても使える構成ですが、運用履歴・Discord channel ID・個人用プロジェクト名が含まれます。公開リポジトリにする場合は、履歴ごとサニタイズした別リポジトリを作ってください。

## できること

- Discordのメンションまたは専用チャンネル投稿からCodexを起動
- botごとに別の作業ディレクトリ、Discord token、セッションファイルを使用
- DiscordスレッドごとにCodex sessionを継続
- 添付ファイルを `_inbox/` に保存し、Codexが `_outbox/` に置いたファイルをDiscordへ返送
- macOS LaunchAgentでGUIセッション配下に常駐
- `kanojo` botでは任意でカード利用サマリー機能を起動

## おすすめのDiscord運用設計

このbot群は「1つの入口チャンネルからbotを呼び、作業ごとは専用フォーラムのスレッドに分ける」運用が一番扱いやすいです。

かーくんの運用例:

| 場所 | 用途 | 使い方 |
|---|---|---|
| `#一般` | bot呼び出しの入口 | botユーザーまたはbotロールをメンションして依頼する |
| `general` bot用フォーラム | 汎用作業 | PC操作、単発調査、どのプロジェクトにも属さない相談 |
| `knowledge-hub` bot用フォーラム | ナレッジ蓄積 | `/play`, `/research`, `/person`, `/career`, `/kindle` などの知識管理 |
| `kanojo` bot用チャンネル/フォーラム | 個人秘書・生活ログ | 予定、カード利用サマリー、日常の細かい相談 |
| `yumekano-coe` bot | 特定プロジェクト専用 | `~/yumekano-agent-CoE` のような1プロジェクトに閉じた開発・調査 |
| `#agent-notify` | botの通知置き場 | 新規スレッド作成、エラー、復旧通知を流す |

おすすめの流れ:

1. `#一般` で `@knowledge-hub-bot このテーマを調べて` のように呼ぶ
2. botが専用フォーラムにスレッドを作る
3. 以後はそのスレッドで続ける
4. 作業が終わったら `!close` で閉じる

これにすると、雑談・依頼入口・作業ログが混ざりにくくなります。友人に共有する場合も、まず `#bot-entry` のような入口チャンネルと、bot別フォーラムを作るのがおすすめです。

## botの使い分け

`config.json` の `bots` で、botごとに作業ディレクトリとDiscordチャンネルを分けます。

かーくんの例:

| bot | 作業ディレクトリ | 向いている依頼 |
|---|---|---|
| `general` | `~` | どのリポジトリにも属さない作業、Mac上の確認、軽い調査 |
| `kb` | `~/knowledge-hub` | 気になることのリサーチ、人物調査、ビジネスアイデア検証、Kindle/本の整理 |
| `kanojo` | `~/kanojo` | 予定調整、個人メモ、カード利用の見返し、生活に近い相談 |
| `yumekano-coe` | `~/yumekano-agent-CoE` | ゆめかのAgent CoE関連の開発・仕様整理 |

友人が自分用に使うなら、最初は2体構成で十分です。

- `general`: 何でも相談・単発作業用
- `work`: 友人のメインプロジェクト用

慣れてから、`research`, `money`, `private`, `side-project` のように増やすと管理しやすいです。

## チャンネルとスレッドの使い方

入口チャンネルでは、長いやり取りを続けない方が運用しやすいです。入口は「どのbotに何を頼むか」だけにして、実作業はスレッドへ移します。

良い使い方:

```text
@knowledge-hub-bot AIエージェント市場の最近の動きを調べて、あとで読み返せる形でまとめて
```

```text
@general-bot このリポジトリのREADMEを友人向けに整えて。秘密情報がないかも見て
```

```text
@kanojo-bot 今日の予定と最近のカード利用を見て、夕方までにやることを整理して
```

避けたい使い方:

- 入口チャンネルで同じ依頼を何度も追記する
- 複数の話題を1スレッドに詰め込む
- 個人情報を扱うbotを誰でも見えるチャンネルで動かす
- `allowed_users` を空のまま共有サーバーに置く

1タスク1スレッドにすると、Codexの会話文脈も壊れにくく、あとから `!sessions` で見返しやすくなります。

## 友人向けの最小構成

友人に渡す場合は、いきなり4 bot構成にしない方が安全です。

最小構成:

```json
{
  "bots": {
    "general": {
      "name": "general",
      "token_keychain_account": "general-bot-token",
      "dir": "~",
      "emoji": "home",
      "control_channel_id": 123456789012345678
    },
    "work": {
      "name": "work",
      "token_keychain_account": "work-bot-token",
      "dir": "~/my-project",
      "emoji": "work",
      "control_channel_id": 234567890123456789
    }
  },
  "notify_channel_id": 345678901234567890,
  "allowed_users": [456789012345678901],
  "auto_pull_before_session": true,
  "worktree_enabled": true,
  "codex_idle_timeout_seconds": 900,
  "codex_hard_timeout_seconds": 3600,
  "codex_max_concurrent_runs": 1,
  "typing_interval_seconds": 20
}
```

友人に最初に作ってもらうDiscordチャンネル:

- `#bot-entry`: botを呼ぶ入口
- `general` forum: 汎用botの作業スレッド置き場
- `work` forum: メインプロジェクト用botの作業スレッド置き場
- `#agent-notify`: エラーや完了通知

`control_channel_id` には、各botの作業スレッドを作りたいフォーラムまたはテキストチャンネルのIDを入れます。入口チャンネルのIDではありません。

## 日常運用

普段見るもの:

```bash
launchctl list | grep discord-bot
tail -f ~/discord-bots/logs/general.err.log
```

Discord側で見るもの:

- `!status`: botがどのディレクトリを見ているか確認
- `!sessions`: 開きっぱなしの作業スレッドを確認
- `!close`: 終わったスレッドを閉じる

更新するとき:

```bash
cd ~/discord-bots
git pull --ff-only
.venv/bin/pip install -r requirements.txt
bash launchd/install-macmini.sh
```

botが反応しないときは、まず以下を見ます。

```bash
launchctl list | grep discord-bot
tail -80 ~/discord-bots/logs/general.err.log
tail -80 ~/discord-bots/logs/kb.err.log
```

よくある原因:

- Discord tokenがKeychainまたは `.env` に無い
- Discord Developer Portalで `MESSAGE CONTENT INTENT` が無効
- botが対象チャンネルを見る権限を持っていない
- `allowed_users` に自分のDiscordユーザーIDが入っていない
- Codex CLIがログアウトしている

## 共有前のセキュリティ確認

このリポジトリでは、実トークンやOAuth tokenをgit管理しない前提です。

git管理しないもの:

- `.env`
- `data/gmail_credentials.json`
- `data/gmail_token.json`
- `data/card.sqlite3`
- `data/epos_storage_state.json`
- `logs/`
- `sessions*.json`
- `.lock-*.pid`
- `.serena/`

注意点:

- `config.json` のDiscord channel IDやbot名は秘密鍵ではありませんが、個人サーバー構成が分かります。友人は必ず自分のDiscordサーバーIDへ差し替えてください。
- `allowed_users` が空配列の場合、botを呼べるユーザー制限が無効です。共有サーバーで使う場合は必ず自分のDiscordユーザーIDを入れてください。
- `PLANS.md` と `PROJECT_LOG/` には過去の運用メモが含まれます。公開配布するなら削除または履歴ごと作り直してください。
- Discord bot token、Gmail OAuth JSON、カード/銀行/ECサイトの認証情報は絶対にcommitしないでください。

管理対象ファイル内にDiscord token形式、GitHub token形式、Google API key形式の実値がないことは確認済みです。

## 前提

- macOS
- Python 3.13系
- Codex CLIにログイン済み
- Discord Developer Portalで作成したBot
- BotのPrivileged Gateway Intentsで `MESSAGE CONTENT INTENT` を有効化
- BotをDiscordサーバーへ招待済み

必要なPython依存:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Codex CLIがない場合:

```bash
npm install -g @openai/codex
codex --version
```

## 初期セットアップ

このプロジェクトのスクリプトは標準で `~/discord-bots` を前提にしています。

```bash
cd ~
git clone git@github.com:ishimikazuki/discord-bots.git discord-bots
cd ~/discord-bots
cp config.example.json config.json
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`config.json` を自分用に編集します。

- `bots.<bot>.name`: 表示名
- `bots.<bot>.token_keychain_account`: Keychainまたは `.env` で使うtoken名
- `bots.<bot>.dir`: Codexを実行する作業ディレクトリ
- `bots.<bot>.emoji`: スレッド名に付ける短い識別子
- `bots.<bot>.control_channel_id`: 専用フォーラム/テキストチャンネルID。未設定ならメンション/DM中心
- `notify_channel_id`: 通知先チャンネルID。不要なら `null`
- `allowed_users`: 実行を許可するDiscordユーザーID。共有サーバーでは空にしない

チャンネルIDやユーザーIDは、Discordの開発者モードを有効にして右クリックからコピーできます。

## Tokenの登録

本番はmacOS Keychain推奨です。

```bash
security add-generic-password -a "general-bot-token" -s "discord-bot" -w "YOUR_DISCORD_BOT_TOKEN" -U
security find-generic-password -a "general-bot-token" -s "discord-bot" -w
```

複数botを使う場合は、`config.json` の `token_keychain_account` ごとに登録します。

ローカル検証だけなら `.env` でも動きます。

```bash
cp .env.example .env
```

`.env` は `bot.py` が直接読むファイルです。shellに `source` しないでください。

## 起動

単体で前面起動:

```bash
.venv/bin/python -u bot.py general
```

macOS LaunchAgentとして起動:

```bash
bash setup-macmini.sh
```

LaunchAgentの処理だけ確認:

```bash
DRY_RUN=1 bash launchd/install-macmini.sh
```

再起動:

```bash
cd ~/discord-bots
git pull --ff-only
.venv/bin/pip install -r requirements.txt
bash launchd/install-macmini.sh
```

停止:

```bash
pkill -f "$HOME/discord-bots/bot.py"
```

確認:

```bash
launchctl list | grep discord-bot
tail -f ~/discord-bots/logs/general.err.log
```

## Discordで使うコマンド

- `!status`: botの設定状態を表示
- `!sessions`: アクティブなスレッドセッション一覧
- `!pull`: botの作業ディレクトリで `git pull --ff-only`
- `!close`: スレッドセッションを閉じて、可能ならスレッドをarchive

通常の依頼は、botをメンションするか、botの専用チャンネル/フォーラムへ投稿します。

## 添付ファイル

ユーザーがDiscordに添付したファイルは、実行ディレクトリの `_inbox/` に保存されます。

CodexからDiscordへ返したいファイルは `_outbox/` に保存してください。botが実行後に送信し、送信済みファイルは削除します。

制限:

- 1ファイル10MBまで
- 1メッセージ10ファイルまで

## カードサマリー機能

`BOT_NAME == "kanojo"` のとき、`card_summary` schedulerが起動します。友人が使わない場合は、`config.json` から `kanojo` botを外してください。

使う場合に必要なもの:

- `card_summary/config.py` の `KANOJO_FORUM_CHANNEL_ID` を自分のチャンネルへ変更
- Gmail OAuth Desktop Client JSON: `data/gmail_credentials.json`
- OAuth後に生成される `data/gmail_token.json`
- Epos Net用Keychain:

```bash
security add-generic-password -a "epos-email" -s "epos-net" -w "YOUR_EMAIL" -U
security add-generic-password -a "epos-pass" -s "epos-net" -w "YOUR_PASSWORD" -U
security add-generic-password -a "epos-cvv" -s "epos-net" -w "YOUR_CVV" -U
```

カードサマリーは個人情報の塊なので、使わない人には設定させないでください。

## テスト

```bash
npm test
```

中身は次を実行します。

```bash
.venv/bin/python -m pytest
bash tests/test_generate_plists.sh
bash tests/test_install_macmini.sh
```

## トラブルシュート

`[FATAL] <account> not found in .env or keychain`

- `config.json` の `token_keychain_account` とKeychainの `-a` が一致しているか確認
- `.env` を使う場合は `account=value` 形式で書く

botが反応しない:

- Discord Developer Portalで `MESSAGE CONTENT INTENT` が有効か確認
- botが対象チャンネルを読める権限を持っているか確認
- `allowed_users` に自分のDiscordユーザーIDが入っているか確認

LaunchAgentでtokenが読めない:

- GUIログイン済みユーザーのKeychainに登録されているか確認
- SSHだけで起動するとKeychainアクセスに失敗することがあります。LaunchAgentは `gui/$UID` ドメインで起動してください。
