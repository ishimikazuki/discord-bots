"""
Discord Bot - Python version (v3)
1 bot = 1 project architecture.
Launch: python bot.py <bot_name>  (e.g. python bot.py general)

Each bot runs as an independent process with its own Discord token,
project directory, and session state.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
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
from codex_errors import describe_codex_failure
from discord_test_auth import (
    is_allowed_bot_test_message,
    parse_id_set,
    strip_test_nonce,
)
from singleton_lock import acquire_or_exit

# launchd captures stderr into <bot>.err.log. Route discord.py internals there
# so gateway reconnect / rate-limit / auth failures are visible post-mortem.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stderr,
)
logging.getLogger("discord").setLevel(logging.INFO)
logging.getLogger("discord.gateway").setLevel(logging.INFO)

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
CODEX_AGENT_NAME = "codex"
CODEX_IDLE_TIMEOUT = CONFIG.get("codex_idle_timeout_seconds", 900)
CODEX_HARD_TIMEOUT = CONFIG.get("codex_hard_timeout_seconds", 3600)
CODEX_MAX_CONCURRENT_RUNS = CONFIG.get("codex_max_concurrent_runs", 1)
CODEX_MODEL = CONFIG.get("codex_model")
CODEX_APP_RESOURCES = "/Applications/Codex.app/Contents/Resources"
TYPING_INTERVAL_SECONDS = CONFIG.get("typing_interval_seconds", 20)
TEST_BOT_AUTHOR_IDS = parse_id_set(os.environ.get("DISCORD_BOT_TEST_AUTHOR_IDS"))
TEST_MESSAGE_NONCE = os.environ.get("DISCORD_BOT_TEST_NONCE")

SESSIONS_FILE = Path(__file__).parent / f"sessions-{BOT_NAME}.json"
_thread_locks: dict[int, asyncio.Lock] = {}
_codex_semaphore = asyncio.Semaphore(CODEX_MAX_CONCURRENT_RUNS)
_pending_recovery_started = False


def get_thread_lock(channel_id: int) -> asyncio.Lock:
    lock = _thread_locks.get(channel_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[channel_id] = lock
    return lock


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


def clear_pending_fields(session: dict) -> None:
    session.pop("pending", None)
    session.pop("pendingPrompt", None)
    session.pop("pendingUserText", None)
    session.pop("pendingStartedAt", None)
    session.pop("pendingSourceMessageId", None)


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
# Codex runner
# ---------------------------------------------------------------------------

async def _kill_proc(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        pass


def build_codex_args(session_id: str | None) -> list[str]:
    args = ["codex", "exec"]
    if session_id:
        args.append("resume")

    args.extend([
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--config",
        'project_doc_fallback_filenames=["CLAUDE.md"]',
        "--config",
        "project_doc_max_bytes=131072",
    ])
    if CODEX_MODEL:
        args.extend(["--model", str(CODEX_MODEL)])

    if session_id:
        args.extend([session_id, "-"])
    else:
        args.append("-")
    return args


async def run_codex_code(work_dir: str, prompt: str, session_id: str | None) -> dict:
    """Run `codex exec` in JSONL mode with idle + hard timeouts.

    Idle timeout kicks in only when nothing is emitted for CODEX_IDLE_TIMEOUT
    seconds, so long-running tasks don't fail as long as Codex keeps making
    progress. CODEX_HARD_TIMEOUT is an absolute cap to prevent runaway runs.
    """
    async with _codex_semaphore:
        return await _run_codex_code_unlocked(work_dir, prompt, session_id)


async def _run_codex_code_unlocked(work_dir: str, prompt: str, session_id: str | None) -> dict:
    args = build_codex_args(session_id)

    home = Path.home()
    env_path = ":".join([
        f"{home}/.npm-global/bin",
        f"{home}/.local/bin",
        f"{home}/.local/node-v22/bin",
        CODEX_APP_RESOURCES,
        os.environ.get("PATH", ""),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ])
    env = {**os.environ, "PATH": env_path}

    codex_path = shutil.which("codex", path=env["PATH"])
    if codex_path:
        args[0] = codex_path
    else:
        raise RuntimeError(f"codex CLI not found on PATH: {env['PATH']}")
    print(
        f"[codex] launching cwd={work_dir} codex={codex_path} "
        f"resume={session_id} model={CODEX_MODEL or '(default)'}",
        file=sys.stderr,
    )

    last_stdout_lines: list[str] = []
    agent_messages: list[str] = []
    thread_id: str | None = None
    usage: dict | None = None
    last_event: dict | None = None

    # JSONL events for tool calls can blow past the 64 KiB default buffer;
    # raise it so readline() doesn't die with a long-chunk error.
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=work_dir,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )

    loop = asyncio.get_event_loop()
    started_at = loop.time()
    last_activity = started_at
    stderr_buf = bytearray()
    stderr_limit = 256 * 1024

    async def drain_stderr() -> None:
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            stderr_buf.extend(chunk)
            if len(stderr_buf) > stderr_limit:
                del stderr_buf[:-stderr_limit]

    async def feed_stdin() -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
                await proc.stdin.wait_closed()
            except Exception:
                pass

    stderr_task = asyncio.create_task(drain_stderr())
    stdin_task = asyncio.create_task(feed_stdin())

    try:
        while True:
            now = loop.time()
            idle_budget = CODEX_IDLE_TIMEOUT - (now - last_activity)
            hard_budget = CODEX_HARD_TIMEOUT - (now - started_at)
            budget = min(idle_budget, hard_budget)

            if budget <= 0:
                await _kill_proc(proc)
                reason = (
                    f"no output for {CODEX_IDLE_TIMEOUT}s"
                    if idle_budget <= hard_budget
                    else f"total runtime exceeded {CODEX_HARD_TIMEOUT}s"
                )
                raise RuntimeError(f"Codex timed out ({reason})")

            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=budget)
            except asyncio.TimeoutError:
                continue

            if not line:
                break

            last_activity = loop.time()

            decoded = line.decode(errors="replace").rstrip()
            last_stdout_lines.append(decoded[:500])
            if len(last_stdout_lines) > 5:
                last_stdout_lines.pop(0)

            try:
                event = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            last_event = event
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id") or thread_id
            elif event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    text = str(item.get("text") or "").strip()
                    if text:
                        agent_messages.append(text)
            elif event.get("type") == "turn.completed":
                usage = event.get("usage") or usage
    except BaseException:
        await _kill_proc(proc)
        stdin_task.cancel()
        stderr_task.cancel()
        raise

    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        await _kill_proc(proc)

    try:
        await asyncio.wait_for(stdin_task, timeout=2)
    except asyncio.TimeoutError:
        stdin_task.cancel()
        try:
            await stdin_task
        except (asyncio.CancelledError, Exception):
            pass
    except (asyncio.CancelledError, Exception):
        pass

    try:
        await asyncio.wait_for(stderr_task, timeout=2)
    except asyncio.TimeoutError:
        stderr_task.cancel()
        try:
            await stderr_task
        except (asyncio.CancelledError, Exception):
            pass
    except (asyncio.CancelledError, Exception):
        pass

    if proc.returncode != 0:
        err = stderr_buf.decode(errors="replace")[-1000:]
        failure = describe_codex_failure(proc.returncode, err, last_event)
        tail = " | ".join(last_stdout_lines) or "(no stdout)"
        sid = session_id or "(new)"
        print(
            f"[codex] exit={proc.returncode} session={sid} cwd={work_dir} "
            f"failure={failure!r} stderr={err!r} last_stdout={tail!r} "
            f"PATH={env.get('PATH', '')[:120]} HOME={env.get('HOME', '')} args={args}",
            file=sys.stderr,
        )
        raise RuntimeError(failure)

    if thread_id is None and session_id is None:
        raise RuntimeError("Codex did not emit a thread.started event")

    return {
        "text": "\n\n".join(agent_messages) or "(no response)",
        "sessionId": thread_id or session_id,
        "cost": 0,
        "usage": usage or {},
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
    """Attach the Discord file-transfer convention to each Codex turn."""
    parts = [
        (
            "[Discord連携ルール]\n"
            "- 添付ファイルがある場合は、作業ディレクトリ直下の `_inbox/` を確認してください。\n"
            "- Discordへ返したい生成物は `_outbox/` に保存してください。Botが実行後に送信します。\n"
            "- 最終応答はDiscordにそのまま投稿される本文として、簡潔に書いてください。"
        )
    ]
    inbox_note = format_inbox_for_prompt(saved)
    if inbox_note:
        parts.append(inbox_note)
    parts.append(f"[ユーザーの依頼]\n{user_text}")
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
                await asyncio.sleep(TYPING_INTERVAL_SECONDS)
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
        agent = s.get("agent") or "legacy"
        agent_label = "" if agent == CODEX_AGENT_NAME else f" [{agent}]"
        lines.append(
            f"  **{s['threadName']}** ({s['messageCount']} msgs, {age}min ago)"
            f"{wt}{agent_label} <#{thread_id}>"
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
        f"  Agent: {CODEX_AGENT_NAME}",
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
    lock = get_thread_lock(thread.id)
    async with lock:
        await _start_session_locked(thread, text, thread_name, trigger_message)


async def _start_session_locked(
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
        "agent": CODEX_AGENT_NAME,
        "threadName": thread_name,
        "createdAt": now_iso(),
        "lastUsed": now_iso(),
        "messageCount": 0,
        "pending": True,
        "pendingStartedAt": now_iso(),
        "pendingUserText": text,
        "pendingSourceMessageId": str(trigger_message.id) if trigger_message else None,
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

    sessions = load_sessions()
    session = sessions.get(str(thread.id), {})
    session.update({
        "workDir": work_dir,
        "worktreePath": worktree_path,
        "pending": True,
        "pendingPrompt": prompt,
        "pendingStartedAt": now_iso(),
        "pendingUserText": text,
        "pendingSourceMessageId": str(trigger_message.id) if trigger_message else None,
    })
    sessions[str(thread.id)] = session
    save_sessions(sessions)

    typing = TypingLoop(thread)
    typing.start()

    try:
        result = await run_codex_code(work_dir, prompt, None)
        typing.stop()

        sessions = load_sessions()
        sessions[str(thread.id)] = {
            "sessionId": result.get("sessionId"),
            "projectDir": PROJECT_DIR,
            "workDir": work_dir,
            "worktreePath": worktree_path,
            "agent": CODEX_AGENT_NAME,
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
        sessions = load_sessions()
        session = sessions.get(str(thread.id))
        if session:
            clear_pending_fields(session)
            session["lastError"] = err_msg
            session["lastUsed"] = now_iso()
            save_sessions(sessions)
        await thread.send(f"❌ Error: {err_msg}")
        await notify(f"❌ [{PROJECT_DISPLAY}] Error: {err_msg}")


# ---------------------------------------------------------------------------
# Main handler: continue session in thread
# ---------------------------------------------------------------------------

async def handle_thread_message(message: discord.Message) -> None:
    lock = get_thread_lock(message.channel.id)
    async with lock:
        await _handle_thread_message_locked(message)


async def _handle_thread_message_locked(message: discord.Message) -> None:
    sessions = load_sessions()
    session = sessions.get(str(message.channel.id))

    if not session:
        print(f"[DEBUG] handle_thread_message: no session for {message.channel.id}, msg={message.id}")
        return  # Silently ignore instead of spamming

    if session.get("sessionId") and session.get("agent") != CODEX_AGENT_NAME:
        await message.channel.send(
            "このスレッドは旧AIセッションなので、Codexでは再開できません。"
            "新しいスレッドで始め直してください。"
        )
        return

    if session.get("pending"):
        clear_pending_fields(session)
        session["lastUsed"] = now_iso()
        save_sessions(sessions)

    work_dir = session.get("workDir", session["projectDir"])

    saved_inbox = await save_inbox_attachments(message, work_dir)
    user_text = message.content.strip()

    # Kanojo bot: inject summary context on the first call of a kanojo-posted thread
    ctx_file = session.get("kanojo_context_file")
    if BOT_NAME == "kanojo" and session.get("sessionId") is None and ctx_file:
        try:
            ctx_text = Path(ctx_file).read_text(encoding="utf-8")
            user_text = (
                "<background>このスレッドは以下のサマリーを Bot が投稿して始まりました。"
                "ユーザーの質問はこのサマリーに関するものとして回答してください。\n\n"
                f"{ctx_text}\n</background>\n\n"
                f"ユーザーの質問: {user_text}"
            )
        except Exception as e:
            print(f"[kanojo] failed to read context file {ctx_file}: {e}", file=sys.stderr)

    prompt = build_prompt_with_inbox(user_text, saved_inbox)

    typing = TypingLoop(message.channel)
    typing.start()

    try:
        result = await run_codex_code(
            work_dir,
            prompt,
            session.get("sessionId"),
        )
        typing.stop()

        if result["sessionId"]:
            session["sessionId"] = result["sessionId"]
        session["agent"] = CODEX_AGENT_NAME
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
        result = await run_codex_code(PROJECT_DIR, message.content.strip(), None)
        typing.stop()
        await send_long_message(message.channel, result["text"])
    except Exception as e:
        typing.stop()
        await message.reply(f"❌ Error: {str(e)[:300]}")


# ---------------------------------------------------------------------------
# Pending session recovery
# ---------------------------------------------------------------------------

async def _fetch_messageable_channel(channel_id: int):
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await client.fetch_channel(channel_id)
    except Exception as e:
        print(f"[recover] failed to fetch channel {channel_id}: {e}", file=sys.stderr)
        return None


async def _recover_pending_session(thread_id: str, session: dict) -> None:
    if session.get("agent") != CODEX_AGENT_NAME or not session.get("pending"):
        return
    if session.get("sessionId"):
        return

    channel = await _fetch_messageable_channel(int(thread_id))
    if channel is None:
        return

    prompt = session.get("pendingPrompt")
    if not prompt:
        await channel.send(
            "ごめん、Botの再起動で途中だった依頼本文を復元できなかったよ。"
            "このスレッドでもう一度送ってくれたら、メンションなしで続きから拾うね。"
        )
        sessions = load_sessions()
        current = sessions.get(thread_id)
        if current and current.get("pending") and not current.get("pendingPrompt"):
            clear_pending_fields(current)
            current["lastError"] = "pending prompt missing after bot restart"
            current["lastUsed"] = now_iso()
            save_sessions(sessions)
        print(f"[recover] missing pendingPrompt thread={thread_id}")
        return

    work_dir = session.get("workDir") or session.get("projectDir") or PROJECT_DIR
    typing = TypingLoop(channel)
    typing.start()
    try:
        result = await run_codex_code(work_dir, prompt, None)
        typing.stop()

        sessions = load_sessions()
        current = sessions.get(thread_id, session)
        current.update({
            "sessionId": result.get("sessionId"),
            "projectDir": current.get("projectDir", PROJECT_DIR),
            "workDir": work_dir,
            "agent": CODEX_AGENT_NAME,
            "lastUsed": now_iso(),
            "messageCount": max(int(current.get("messageCount", 0)), 0) + 1,
        })
        clear_pending_fields(current)
        sessions[thread_id] = current
        save_sessions(sessions)

        await send_long_message(channel, result["text"])
        sent = await send_outbox_files(channel, work_dir)

        cost_str = f" (${result['cost']:.4f})" if result.get("cost") else ""
        file_str = f" +{sent}files" if sent else ""
        print(f"[recover] {current.get('threadName', thread_id)} -> {len(result['text'])} chars{cost_str}{file_str}")
        await notify(f"♻️ [{PROJECT_DISPLAY}] Recovered: **{current.get('threadName', thread_id)}**{cost_str}{file_str}")
    except Exception as e:
        typing.stop()
        err_msg = str(e)[:300]
        print(f"[recover] Error: {e}", file=sys.stderr)
        sessions = load_sessions()
        current = sessions.get(thread_id)
        if current:
            clear_pending_fields(current)
            current["lastError"] = err_msg
            current["lastUsed"] = now_iso()
            save_sessions(sessions)
        await channel.send(f"❌ Error: {err_msg}")
        await notify(f"❌ [{PROJECT_DISPLAY}] Recovery error in **{session.get('threadName', thread_id)}**: {err_msg}")


async def recover_pending_sessions() -> None:
    sessions = load_sessions()
    pending = [
        (thread_id, session)
        for thread_id, session in sessions.items()
        if session.get("pending") and session.get("agent") == CODEX_AGENT_NAME
    ]
    if not pending:
        return
    print(f"[recover] pending sessions: {len(pending)}")
    for thread_id, session in pending:
        lock = get_thread_lock(int(thread_id))
        async with lock:
            await _recover_pending_session(thread_id, session)


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
    global _pending_recovery_started
    # Startup info goes to the per-bot log file; posting to #agent-notify on
    # every boot floods the channel when launchd restart-loops a crashing bot.
    print(f"[{BOT_NAME}] Logged in as {client.user}")
    print(f"[{BOT_NAME}] Project: {PROJECT_DISPLAY} -> {PROJECT_DIR}")
    print(f"[{BOT_NAME}] Control channel: {CONTROL_CHANNEL_ID or 'any (mention or DM)'}")
    print(f"[{BOT_NAME}] Auto-pull: {AUTO_PULL} | Worktree: {WORKTREE_ENABLED}")

    if not _pending_recovery_started:
        _pending_recovery_started = True
        asyncio.create_task(recover_pending_sessions())

    if BOT_NAME == "kanojo":
        from card_summary.scheduler import (
            start_scheduler, post_to_kanojo_forum, register_kanojo_session,
        )
        from card_summary.config import KANOJO_FORUM_CHANNEL_ID

        async def _fetch(since):
            from card_summary.gmail_fetcher import authenticate, build_service, fetch_new_since
            from card_summary.config import GMAIL_QUERY
            creds = authenticate()
            svc = build_service(creds)
            return list(fetch_new_since(svc, GMAIL_QUERY, since))

        def _llm(merchant: str) -> str:
            # Initial implementation: bucket every unknown merchant as 'その他'.
            # The categorizer caches the result so each unknown merchant only hits this once.
            # Replace this stub with a small LLM classifier when budget allows; the contract
            # is `(merchant: str) -> category in CATEGORIES`. See spec §8 for the prompt.
            return "その他"

        async def _post(thread_name, body):
            return await post_to_kanojo_forum(client, KANOJO_FORUM_CHANNEL_ID, thread_name, body)

        async def _register(thread, slot, summary_text):
            await register_kanojo_session(
                SESSIONS_FILE, thread, slot, summary_text, PROJECT_DIR
            )

        asyncio.create_task(start_scheduler(
            fetch_new=_fetch, llm_fn=_llm,
            post_to_forum=_post, register_session=_register,
        ))
        print(f"[{BOT_NAME}] card_summary scheduler started")


@client.event
async def on_disconnect():
    # discord.py auto-reconnects, but we want the kill-the-process class of
    # disconnects visible in err.log so we can spot restart-loop causes.
    print(f"[{BOT_NAME}] DISCONNECTED from Discord gateway", file=sys.stderr)


@client.event
async def on_resumed():
    print(f"[{BOT_NAME}] RESUMED gateway session", file=sys.stderr)


@client.event
async def on_error(event, *args, **kwargs):
    # Default handler only prints to stderr without the bot name prefix.
    # We want an easy grep pattern so yumekano-coe-style crashes are obvious.
    print(
        f"[{BOT_NAME}] UNHANDLED in {event}: {traceback.format_exc()}",
        file=sys.stderr,
    )


@client.event
async def on_message(message: discord.Message):
    try:
        await _handle_message(message)
    except Exception:
        print(
            f"[{BOT_NAME}] UNHANDLED in on_message "
            f"(channel={message.channel.id} msg={message.id}):\n"
            f"{traceback.format_exc()}",
            file=sys.stderr,
        )


async def _handle_message(message: discord.Message):
    is_test_bot_message = is_allowed_bot_test_message(
        author_is_bot=message.author.bot,
        author_id=message.author.id,
        content=message.content or "",
        nonce=TEST_MESSAGE_NONCE,
        allowed_author_ids=TEST_BOT_AUTHOR_IDS,
    )
    if message.author.bot and not is_test_bot_message:
        return
    message_content = strip_test_nonce(message.content or "", TEST_MESSAGE_NONCE)
    if is_test_bot_message:
        print(f"[test] accepted bot-authored test message from {message.author.id}")

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
    mentions_another_bot = any(
        getattr(user, "bot", False) and user.id != client.user.id
        for user in message.mentions
    ) or any(
        getattr(role, "managed", False) and role.id not in my_role_ids
        for role in message.role_mentions
    )

    is_dm = message.channel.type == ChannelType.private
    is_thread = message.channel.type in (ChannelType.public_thread, ChannelType.private_thread)
    is_guild_text = message.channel.type == ChannelType.text

    if is_guild_text:
        content = strip_mentions(
            message_content, {client.user.id}, my_role_ids
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

        if CONTROL_CHANNEL_ID is not None and not is_our_channel and not is_mention:
            # A stale session entry must not let this bot keep responding in
            # another bot's forum. Cross-bot contamination was the source of
            # General Bot replying inside Kanojo/KB-owned threads.
            return
        if is_our_channel and mentions_another_bot and not is_mention:
            return

        content = strip_mentions(message_content, {client.user.id}, my_role_ids)
        if not content:
            content = message_content.strip()
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
    acquire_or_exit(Path(__file__).parent, BOT_NAME)
    print(f"Starting bot [{BOT_NAME}] ({PROJECT_DISPLAY})...")
    client.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
