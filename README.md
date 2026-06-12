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
