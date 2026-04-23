"""
Discord Bot - Python version (v3)
1 bot = 1 project architecture.
Launch: python bot.py <bot_name>  (e.g. python bot.py general)

Each bot runs as an independent process with its own Discord token,
project directory, and session state.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import ChannelType, Intents

from mention_helpers import is_bot_addressed, strip_mentions
from attachments import (
    chunk_for_messages,
    filter_sendable,
    format_inbox_for_prompt,
)

# ---------------------------------------------------------------------------
# Boot: resolve bot name from argv
# ---------------------------------------------------------------------------

CONFIG_FILE = Path(__file__).parent / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        print(f"[FATAL] config.json load failed: {e}", file=sys.stderr)
        sys.exit(1)


CONFIG = load_config()

if len(sys.argv) < 2 or sys.argv[1] not in CONFIG["bots"]:
    available = ", ".join(CONFIG["bots"].keys())
    print(f"Usage: python bot.py <bot_name>", file=sys.stderr)
    print(f"Available bots: {available}", file=sys.stderr)
    sys.exit(1)

BOT_NAME = sys.argv[1]
BOT_CONFIG = CONFIG["bots"][BOT_NAME]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_from_keychain(account: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", "discord-bot", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def get_from_env_file(account: str) -> str | None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            if key.strip() == account:
                return val.strip()
    return None


BOT_TOKEN = get_from_env_file(BOT_CONFIG["token_keychain_account"]) or get_from_keychain(BOT_CONFIG["token_keychain_account"])
if not BOT_TOKEN:
    print(f"[FATAL] {BOT_CONFIG['token_keychain_account']} not found in .env or keychain", file=sys.stderr)
    sys.exit(1)

PROJECT_DIR = str(Path(BOT_CONFIG["dir"]).expanduser())
PROJECT_EMOJI = BOT_CONFIG.get("emoji", "🤖")
PROJECT_DISPLAY = BOT_CONFIG.get("name", BOT_NAME)
CONTROL_CHANNEL_ID: int | None = BOT_CONFIG.get("control_channel_id")

ALLOWED_USERS: list[int] = CONFIG.get("allowed_users", [])
NOTIFY_CHANNEL_ID: int | None = CONFIG.get("notify_channel_id")
AUTO_PULL = CONFIG.get("auto_pull_before_session", True)
WORKTREE_ENABLED = CONFIG.get("worktree_enabled", True)
CLAUDE_IDLE_TIMEOUT = CONFIG.get(
    "claude_idle_timeout_seconds",
    CONFIG.get("claude_timeout_seconds", 300),
)
CLAUDE_HARD_TIMEOUT = CONFIG.get("claude_hard_timeout_seconds", 3600)
CLAUDE_MAX_TURNS = CONFIG.get("claude_max_turns", 25)

SESSIONS_FILE = Path(__file__).parent / f"sessions-{BOT_NAME}.json"


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def load_sessions() -> dict:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return {}


def save_sessions(sessions: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git_pull(project_dir: str) -> str | None:
    if not (Path(project_dir) / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=project_dir, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            pulled = result.stdout.strip()
            if "Already up to date" not in pulled:
                print(f"[git] pulled: {pulled[:100]}")
            return None
        return result.stderr.strip()[:200]
    except Exception as e:
        return str(e)[:200]


def create_worktree(project_dir: str, thread_id: str) -> str | None:
    if not WORKTREE_ENABLED:
        return None
    if not (Path(project_dir) / ".git").exists():
        return None

    worktree_dir = Path(project_dir) / ".worktrees" / f"thread-{thread_id}"
    if worktree_dir.exists():
        return str(worktree_dir)

    branch_name = f"thread/{thread_id}"
    try:
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"],
            cwd=project_dir, capture_output=True, text=True, timeout=30, check=True,
        )
        print(f"[worktree] created {worktree_dir}")
        return str(worktree_dir)
    except subprocess.CalledProcessError:
        try:
            subprocess.run(
                ["git", "worktree", "add", str(worktree_dir), branch_name],
                cwd=project_dir, capture_output=True, text=True, timeout=30, check=True,
            )
            return str(worktree_dir)
        except Exception as e:
            print(f"[worktree] failed: {e}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[worktree] failed: {e}", file=sys.stderr)
        return None


def remove_worktree(project_dir: str, thread_id: str) -> None:
    worktree_dir = Path(project_dir) / ".worktrees" / f"thread-{thread_id}"
    branch_name = f"thread/{thread_id}"
    if not worktree_dir.exists():
        return
    try:
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_dir), "--force"],
            cwd=project_dir, capture_output=True, text=True, timeout=15,
        )
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=project_dir, capture_output=True, text=True, timeout=10,
        )
        print(f"[worktree] removed {worktree_dir}")
    except Exception as e:
        print(f"[worktree] remove failed: {e}", file=sys.stderr)
        shutil.rmtree(worktree_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Claude Code runner
# ---------------------------------------------------------------------------

async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass


async def run_claude_code(work_dir: str, prompt: str, session_id: str | None) -> dict:
    """Run `claude` CLI in stream-json mode with idle + hard timeouts.

    Idle timeout kicks in only when nothing is emitted for CLAUDE_IDLE_TIMEOUT
    seconds, so long-running tasks don't fail as long as Claude keeps making
    progress. CLAUDE_HARD_TIMEOUT is an absolute cap to prevent runaway runs.
    """
    args = [
        "claude",
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(CLAUDE_MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]
    if session_id:
        args.extend(["--resume", session_id])

    home = Path.home()
    env = {**os.environ, "PATH": f"{home}/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=work_dir,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    loop = asyncio.get_event_loop()
    started_at = loop.time()
    last_activity = started_at
    result_event: dict | None = None
    stderr_buf = bytearray()

    async def drain_stderr() -> None:
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            stderr_buf.extend(chunk)

    stderr_task = asyncio.create_task(drain_stderr())

    try:
        while True:
            now = loop.time()
            idle_budget = CLAUDE_IDLE_TIMEOUT - (now - last_activity)
            hard_budget = CLAUDE_HARD_TIMEOUT - (now - started_at)
            budget = min(idle_budget, hard_budget)

            if budget <= 0:
                await _kill_proc(proc)
                reason = (
                    f"no output for {CLAUDE_IDLE_TIMEOUT}s"
                    if idle_budget <= hard_budget
                    else f"total runtime exceeded {CLAUDE_HARD_TIMEOUT}s"
                )
                raise RuntimeError(f"Claude Code timed out ({reason})")

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=budget)
            except asyncio.TimeoutError:
                continue

            if not line:
                break

            last_activity = loop.time()

            try:
                event = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            if event.get("type") == "result":
                result_event = event
    except BaseException:
        await _kill_proc(proc)
        raise
    finally:
        if not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):
                pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        await _kill_proc(proc)

    if proc.returncode != 0:
        err = stderr_buf.decode(errors="replace")[:300]
        print(f"[claude] exit={proc.returncode} stderr={err}", file=sys.stderr)
        raise RuntimeError(f"Claude Code exited {proc.returncode}: {err or '(no stderr)'}")

    if result_event is None:
        raise RuntimeError("Claude Code did not emit a result event")

    return {
        "text": result_event.get("result") or "(no response)",
        "sessionId": result_event.get("session_id"),
        "cost": result_event.get("total_cost_usd", 0),
    }


# ---------------------------------------------------------------------------
# Discord message helpers
# ---------------------------------------------------------------------------

async def send_long_message(channel: discord.abc.Messageable, text: str) -> None:
    remaining = text
    while remaining:
        if len(remaining) <= 2000:
            await channel.send(remaining)
            break
        split_at = remaining.rfind("\n", 0, 2000)
        if split_at == -1 or split_at < 1000:
            split_at = 2000
        await channel.send(remaining[:split_at])
        remaining = remaining[split_at:]


async def save_inbox_attachments(
    message: discord.Message, work_dir: str
) -> list[Path]:
    """Download message.attachments to work_dir/_inbox/. Returns saved paths."""
    if not message.attachments:
        return []
    inbox = Path(work_dir) / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for att in message.attachments:
        target = inbox / att.filename
        try:
            await att.save(target)
            saved.append(target)
        except Exception as e:
            print(f"[inbox] failed to save {att.filename}: {e}", file=sys.stderr)
    return saved


async def send_outbox_files(
    channel: discord.abc.Messageable, work_dir: str
) -> int:
    """Send every file under work_dir/_outbox/ back to the channel. Files are
    removed once successfully uploaded. Returns count sent."""
    outbox = Path(work_dir) / "_outbox"
    if not outbox.is_dir():
        return 0
    files = sorted(p for p in outbox.rglob("*") if p.is_file())
    if not files:
        return 0

    sendable, rejected = filter_sendable(files)
    for path, reason in rejected:
        await channel.send(f"⚠️ `{path.name}` is too large to attach ({reason}).")

    sent_count = 0
    for batch in chunk_for_messages(sendable):
        discord_files = [discord.File(str(p), filename=p.name) for p in batch]
        await channel.send(files=discord_files)
        for p in batch:
            try:
                p.unlink()
            except OSError:
                pass
        sent_count += len(batch)
    return sent_count


def build_prompt_with_inbox(user_text: str, saved: list[Path]) -> str:
    """Glue inbox listing onto the user prompt. The _outbox/ convention is
    declared once per session to keep Claude aware of where to write output."""
    preamble = (
        "[Discord 連携ルール] 出力ファイル (PDF/画像/CSV 等) は _outbox/ 以下に "
        "書き出してね。ユーザーに自動で添付されるよ。"
    )
    inbox = format_inbox_for_prompt(saved)
    parts = [preamble]
    if inbox:
        parts.append(inbox)
    parts.append(user_text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

async def notify(text: str) -> None:
    if not NOTIFY_CHANNEL_ID:
        return
    try:
        channel = client.get_channel(NOTIFY_CHANNEL_ID)
        if channel:
            await send_long_message(channel, text)
    except Exception as e:
        print(f"[notify] failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Typing indicator
# ---------------------------------------------------------------------------

class TypingLoop:
    def __init__(self, channel: discord.abc.Messageable):
        self.channel = channel
        self._task: asyncio.Task | None = None

    async def _loop(self):
        try:
            while True:
                try:
                    await self.channel.typing()
                except Exception:
                    pass
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    def start(self):
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

def parse_command(content: str) -> dict:
    stripped = content.strip()
    if stripped == "!sessions":
        return {"type": "sessions"}
    if stripped == "!close":
        return {"type": "close"}
    if stripped == "!pull":
        return {"type": "pull"}
    if stripped == "!status":
        return {"type": "status"}
    return {"type": "message", "text": stripped}


# ---------------------------------------------------------------------------
# Thread name builder
# ---------------------------------------------------------------------------

def build_thread_name(text: str) -> str:
    short = text[:80].replace("\n", " ")
    return f"{PROJECT_EMOJI} {short}"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_sessions(channel: discord.abc.Messageable) -> None:
    sessions = load_sessions()
    if not sessions:
        await channel.send(f"{PROJECT_EMOJI} **{PROJECT_DISPLAY}**: no active sessions")
        return

    lines = []
    for thread_id, s in sessions.items():
        last_used = datetime.fromisoformat(s["lastUsed"].replace("Z", "+00:00"))
        age = int((datetime.now(timezone.utc) - last_used).total_seconds() / 60)
        wt = " 🌿" if s.get("worktreePath") else ""
        lines.append(
            f"  **{s['threadName']}** ({s['messageCount']} msgs, {age}min ago){wt} <#{thread_id}>"
        )
    await send_long_message(
        channel,
        f"{PROJECT_EMOJI} **{PROJECT_DISPLAY}** sessions:\n" + "\n".join(lines),
    )


async def handle_pull(channel: discord.abc.Messageable) -> None:
    err = git_pull(PROJECT_DIR)
    status = "✅ up to date" if err is None else f"❌ {err}"
    await channel.send(f"{PROJECT_EMOJI} **{PROJECT_DISPLAY}** git pull: {status}")


async def handle_status(channel: discord.abc.Messageable) -> None:
    sessions = load_sessions()
    lines = [
        f"{PROJECT_EMOJI} **{PROJECT_DISPLAY}** status:",
        f"  Dir: `{PROJECT_DIR}`",
        f"  Sessions: {len(sessions)}",
        f"  Auto-pull: {'on' if AUTO_PULL else 'off'}",
        f"  Worktree: {'on' if WORKTREE_ENABLED else 'off'}",
        f"  Notify: {'<#' + str(NOTIFY_CHANNEL_ID) + '>' if NOTIFY_CHANNEL_ID else 'off'}",
    ]
    await channel.send("\n".join(lines))


async def handle_close(thread: discord.Thread) -> None:
    sessions = load_sessions()
    session = sessions.get(str(thread.id))

    if session:
        if session.get("worktreePath"):
            remove_worktree(session["projectDir"], str(thread.id))
        del sessions[str(thread.id)]
        save_sessions(sessions)
        await notify(f"🔒 [{PROJECT_DISPLAY}] Session closed: **{session.get('threadName', '?')}** ({session['messageCount']} msgs)")

    await thread.send("Session closed.")
    try:
        await thread.edit(archived=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main handler: new session via thread (text channel)
# ---------------------------------------------------------------------------

async def handle_new_session(message: discord.Message, text: str) -> None:
    thread_name = build_thread_name(text)

    # Prefer creating the thread inside this bot's dedicated forum so the
    # conversation lives next to its siblings (see: mention-in-#一般 → forum).
    forum = client.get_channel(CONTROL_CHANNEL_ID) if CONTROL_CHANNEL_ID else None
    if isinstance(forum, discord.ForumChannel):
        created = await forum.create_thread(
            name=thread_name,
            content=f"From {message.author.mention} in {message.channel.mention}: {text}",
            auto_archive_duration=1440,
        )
        thread = created.thread
        await message.reply(
            f"→ {thread.mention} で続きはこっちでお話しするよ♡",
            mention_author=False,
        )
    else:
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)

    await _start_session(thread, text, thread_name, trigger_message=message)


# ---------------------------------------------------------------------------
# Main handler: new session via forum post
# ---------------------------------------------------------------------------

async def handle_forum_new_session(message: discord.Message, text: str) -> None:
    thread = message.channel  # Already a thread (forum post)
    await _start_session(thread, text, thread.name, trigger_message=message)


async def _start_session(
    thread, text: str, thread_name: str, trigger_message: discord.Message | None = None
) -> None:
    # Reserve session immediately to prevent duplicate processing
    sessions = load_sessions()
    if str(thread.id) in sessions:
        return  # Already being processed
    sessions[str(thread.id)] = {
        "sessionId": None,
        "projectDir": PROJECT_DIR,
        "workDir": PROJECT_DIR,
        "worktreePath": None,
        "threadName": thread_name,
        "createdAt": now_iso(),
        "lastUsed": now_iso(),
        "messageCount": 0,
        "pending": True,
    }
    save_sessions(sessions)

    if AUTO_PULL:
        err = git_pull(PROJECT_DIR)
        if err:
            await thread.send(f"⚠️ git pull failed: {err}")

    worktree_path = create_worktree(PROJECT_DIR, str(thread.id))
    work_dir = worktree_path or PROJECT_DIR

    saved_inbox: list[Path] = []
    if trigger_message is not None:
        saved_inbox = await save_inbox_attachments(trigger_message, work_dir)
    prompt = build_prompt_with_inbox(text, saved_inbox)

    typing = TypingLoop(thread)
    typing.start()

    try:
        result = await run_claude_code(work_dir, prompt, None)
        typing.stop()

        sessions = load_sessions()
        sessions[str(thread.id)] = {
            "sessionId": result.get("sessionId"),
            "projectDir": PROJECT_DIR,
            "workDir": work_dir,
            "worktreePath": worktree_path,
            "threadName": thread_name,
            "createdAt": now_iso(),
            "lastUsed": now_iso(),
            "messageCount": 1,
        }
        save_sessions(sessions)

        await send_long_message(thread, result["text"])
        sent = await send_outbox_files(thread, work_dir)

        cost_str = f" (${result['cost']:.4f})" if result.get("cost") else ""
        file_str = f" +{sent}files" if sent else ""
        print(f"[new] {thread_name} -> {len(result['text'])} chars{cost_str}{file_str}")
        await notify(f"✅ [{PROJECT_DISPLAY}] New: **{thread_name}**{cost_str}{file_str}")

    except Exception as e:
        typing.stop()
        err_msg = str(e)[:300]
        print(f"[new] Error: {e}", file=sys.stderr)
        await thread.send(f"❌ Error: {err_msg}")
        await notify(f"❌ [{PROJECT_DISPLAY}] Error: {err_msg}")


# ---------------------------------------------------------------------------
# Main handler: continue session in thread
# ---------------------------------------------------------------------------

async def handle_thread_message(message: discord.Message) -> None:
    sessions = load_sessions()
    session = sessions.get(str(message.channel.id))

    if not session:
        print(f"[DEBUG] handle_thread_message: no session for {message.channel.id}, msg={message.id}")
        return  # Silently ignore instead of spamming

    work_dir = session.get("workDir", session["projectDir"])

    saved_inbox = await save_inbox_attachments(message, work_dir)
    prompt = build_prompt_with_inbox(message.content.strip(), saved_inbox)

    typing = TypingLoop(message.channel)
    typing.start()

    try:
        result = await run_claude_code(
            work_dir,
            prompt,
            session["sessionId"],
        )
        typing.stop()

        if result["sessionId"]:
            session["sessionId"] = result["sessionId"]
        session["lastUsed"] = now_iso()
        session["messageCount"] += 1
        save_sessions(sessions)

        await send_long_message(message.channel, result["text"])
        sent = await send_outbox_files(message.channel, work_dir)

        cost_str = f" (${result['cost']:.4f})" if result.get("cost") else ""
        file_str = f" +{sent}files" if sent else ""
        print(f"[cont] {session['threadName']} msg#{session['messageCount']}{cost_str}{file_str}")

    except Exception as e:
        typing.stop()
        err_msg = str(e)[:300]
        print(f"[cont] Error: {e}", file=sys.stderr)
        await message.channel.send(f"❌ Error: {err_msg}")
        await notify(f"❌ [{PROJECT_DISPLAY}] Error in **{session['threadName']}**: {err_msg}")


# ---------------------------------------------------------------------------
# Main handler: DM
# ---------------------------------------------------------------------------

async def handle_dm(message: discord.Message) -> None:
    typing = TypingLoop(message.channel)
    typing.start()

    try:
        result = await run_claude_code(PROJECT_DIR, message.content.strip(), None)
        typing.stop()
        await send_long_message(message.channel, result["text"])
    except Exception as e:
        typing.stop()
        await message.reply(f"❌ Error: {str(e)[:300]}")


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True

client = discord.Client(intents=intents)

# Dedup: prevent processing the same message multiple times
_processed_messages: set[int] = set()


@client.event
async def on_ready():
    print(f"[{BOT_NAME}] Logged in as {client.user}")
    print(f"[{BOT_NAME}] Project: {PROJECT_DISPLAY} -> {PROJECT_DIR}")
    print(f"[{BOT_NAME}] Control channel: {CONTROL_CHANNEL_ID or 'any (mention or DM)'}")
    print(f"[{BOT_NAME}] Auto-pull: {AUTO_PULL} | Worktree: {WORKTREE_ENABLED}")
    await notify(f"🟢 [{PROJECT_DISPLAY}] Bot started: {client.user}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.id in _processed_messages:
        return
    _processed_messages.add(message.id)
    if len(_processed_messages) > 1000:
        _processed_messages.clear()
    if ALLOWED_USERS and message.author.id not in ALLOWED_USERS:
        return

    # Collect mention info once. Role mentions make `@bot-name` (role) work
    # the same as pinging the bot user directly.
    my_role_ids: set[int] = set()
    if message.guild and message.guild.me:
        my_role_ids = {r.id for r in message.guild.me.roles if not r.is_default()}
    user_mention_ids = {u.id for u in message.mentions}
    role_mention_ids = {r.id for r in message.role_mentions}
    is_mention = is_bot_addressed(
        user_mention_ids, role_mention_ids, client.user.id, my_role_ids
    )

    is_dm = message.channel.type == ChannelType.private
    is_thread = message.channel.type in (ChannelType.public_thread, ChannelType.private_thread)
    is_guild_text = message.channel.type == ChannelType.text

    if is_guild_text:
        content = strip_mentions(
            message.content, {client.user.id}, my_role_ids
        )
        if not content:
            content = "hello"

        cmd = parse_command(content)

        # Utility commands
        if cmd["type"] == "sessions":
            await handle_sessions(message.channel)
            return
        if cmd["type"] == "pull":
            await handle_pull(message.channel)
            return
        if cmd["type"] == "status":
            await handle_status(message.channel)
            return

        # Only respond in control channel (if set) or to mentions
        is_control = CONTROL_CHANNEL_ID and message.channel.id == CONTROL_CHANNEL_ID

        if not (is_mention or is_control):
            return

        text = cmd.get("text") or content
        await handle_new_session(message, text)
        return

    if is_thread:
        # Only respond to threads in our control channel (forum) or mentions
        parent = getattr(message.channel, 'parent', None)
        parent_id = getattr(parent, 'id', None) or getattr(message.channel, 'parent_id', None)
        is_our_channel = CONTROL_CHANNEL_ID and parent_id == CONTROL_CHANNEL_ID
        print(f"[thread] parent_id={parent_id} control={CONTROL_CHANNEL_ID} is_ours={is_our_channel} mention={is_mention}")

        if not is_our_channel and not is_mention:
            # Check if we have an existing session for this thread
            sessions = load_sessions()
            if str(message.channel.id) not in sessions:
                return  # Not our thread, ignore silently

        content = strip_mentions(message.content, {client.user.id}, my_role_ids)
        if not content:
            content = message.content.strip()
        cmd = parse_command(content)
        if cmd["type"] == "close":
            await handle_close(message.channel)
            return
        if cmd["type"] == "sessions":
            await handle_sessions(message.channel)
            return

        # Existing session → continue, new thread in our channel → start session
        sessions = load_sessions()
        if str(message.channel.id) in sessions:
            await handle_thread_message(message)
        elif is_our_channel:
            await handle_forum_new_session(message, content)
        elif is_mention:
            await message.reply(f"No active session. Post in <#{CONTROL_CHANNEL_ID}> to start one.")
        return

    if is_dm:
        await handle_dm(message)
        return


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def main():
    print(f"Starting bot [{BOT_NAME}] ({PROJECT_DISPLAY})...")
    client.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
