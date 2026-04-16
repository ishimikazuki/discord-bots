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


BOT_TOKEN = get_from_keychain(BOT_CONFIG["token_keychain_account"])
if not BOT_TOKEN:
    print(f"[FATAL] {BOT_CONFIG['token_keychain_account']} not found in keychain", file=sys.stderr)
    sys.exit(1)

PROJECT_DIR = str(Path(BOT_CONFIG["dir"]).expanduser())
PROJECT_EMOJI = BOT_CONFIG.get("emoji", "🤖")
PROJECT_DISPLAY = BOT_CONFIG.get("name", BOT_NAME)
CONTROL_CHANNEL_ID: int | None = BOT_CONFIG.get("control_channel_id")

ALLOWED_USERS: list[int] = CONFIG.get("allowed_users", [])
NOTIFY_CHANNEL_ID: int | None = CONFIG.get("notify_channel_id")
AUTO_PULL = CONFIG.get("auto_pull_before_session", True)
WORKTREE_ENABLED = CONFIG.get("worktree_enabled", True)
CLAUDE_TIMEOUT = CONFIG.get("claude_timeout_seconds", 300)
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

async def run_claude_code(work_dir: str, prompt: str, session_id: str | None) -> dict:
    args = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", str(CLAUDE_MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]
    if session_id:
        args.extend(["--resume", session_id])

    env = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=work_dir,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLAUDE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"Claude Code timed out ({CLAUDE_TIMEOUT}s)")

    if proc.returncode == 0:
        try:
            data = json.loads(stdout.decode())
            return {
                "text": data.get("result") or "(no response)",
                "sessionId": data.get("session_id"),
                "cost": data.get("total_cost_usd", 0),
            }
        except json.JSONDecodeError:
            return {"text": stdout.decode().strip() or "(no response)", "sessionId": None, "cost": 0}
    else:
        err = stderr.decode()[:500]
        raise RuntimeError(f"Claude Code exited {proc.returncode}: {err}")


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
# Main handler: new session via thread
# ---------------------------------------------------------------------------

async def handle_new_session(message: discord.Message, text: str) -> None:
    thread_name = build_thread_name(text)
    thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)

    if AUTO_PULL:
        err = git_pull(PROJECT_DIR)
        if err:
            await thread.send(f"⚠️ git pull failed: {err}")

    worktree_path = create_worktree(PROJECT_DIR, str(thread.id))
    work_dir = worktree_path or PROJECT_DIR

    typing = TypingLoop(thread)
    typing.start()

    try:
        result = await run_claude_code(work_dir, text, None)
        typing.stop()

        if result["sessionId"]:
            sessions = load_sessions()
            sessions[str(thread.id)] = {
                "sessionId": result["sessionId"],
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

        cost_str = f" (${result['cost']:.4f})" if result.get("cost") else ""
        print(f"[new] {thread_name} -> {len(result['text'])} chars{cost_str}")
        await notify(f"✅ [{PROJECT_DISPLAY}] New: **{thread_name}**{cost_str}")

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
        await message.reply("No active session. Start a new one in the channel.")
        return

    work_dir = session.get("workDir", session["projectDir"])

    typing = TypingLoop(message.channel)
    typing.start()

    try:
        result = await run_claude_code(
            work_dir,
            message.content.strip(),
            session["sessionId"],
        )
        typing.stop()

        if result["sessionId"]:
            session["sessionId"] = result["sessionId"]
        session["lastUsed"] = now_iso()
        session["messageCount"] += 1
        save_sessions(sessions)

        await send_long_message(message.channel, result["text"])

        cost_str = f" (${result['cost']:.4f})" if result.get("cost") else ""
        print(f"[cont] {session['threadName']} msg#{session['messageCount']}{cost_str}")

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
    if ALLOWED_USERS and message.author.id not in ALLOWED_USERS:
        return

    is_dm = message.channel.type == ChannelType.private
    is_thread = message.channel.type in (ChannelType.public_thread, ChannelType.private_thread)
    is_guild_text = message.channel.type == ChannelType.text

    if is_guild_text:
        content = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()
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
        is_mention = client.user in message.mentions
        is_control = CONTROL_CHANNEL_ID and message.channel.id == CONTROL_CHANNEL_ID

        if not (is_mention or is_control):
            return

        text = cmd.get("text") or content
        await handle_new_session(message, text)
        return

    if is_thread:
        content = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()
        cmd = parse_command(content)
        if cmd["type"] == "close":
            await handle_close(message.channel)
            return
        if cmd["type"] == "sessions":
            await handle_sessions(message.channel)
            return
        await handle_thread_message(message)
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
