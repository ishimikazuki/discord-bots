"""
Discord Bot - Python version
Bridges Discord messages to Claude Code CLI via subprocess.
Thread-based session management with per-project working directories.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import discord
from discord import ChannelType, Intents, Interaction

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_from_keychain(account: str) -> str | None:
    """Retrieve a password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", "discord-bot", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


BOT_TOKEN = get_from_keychain("general-bot-token")
if not BOT_TOKEN:
    print("Failed to get general-bot-token from keychain", file=sys.stderr)
    sys.exit(1)

# Project definitions -- add new projects here
PROJECTS = {
    "kb": {
        "name": "knowledge-hub",
        "dir": str(Path.home() / "knowledge-hub"),
        "emoji": "\U0001F4DA",  # books
    },
    "general": {
        "name": "general",
        "dir": str(Path.home()),
        "emoji": "\U0001F3E0",  # house
    },
}
DEFAULT_PROJECT = "general"

# Allowed user IDs (Discord snowflakes). Empty = allow all.
ALLOWED_USERS: list[int] = []

# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

SESSIONS_FILE = Path(__file__).parent / "sessions.json"


def load_sessions() -> dict:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return {}


def save_sessions(sessions: dict) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Claude Code runner
# ---------------------------------------------------------------------------

async def run_claude_code(project_dir: str, prompt: str, session_id: str | None) -> dict:
    """Spawn claude CLI and return {text, sessionId, cost}."""
    args = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", "25",
        "--permission-mode", "bypassPermissions",
    ]
    if session_id:
        args.extend(["--resume", session_id])

    env = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=project_dir,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("Claude Code timed out (5 min)")

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
    """Send a message, splitting at 2000-char boundaries on newlines."""
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
# Command parsing
# ---------------------------------------------------------------------------

def parse_command(content: str) -> dict:
    stripped = content.strip()

    if stripped == "!sessions":
        return {"type": "sessions"}
    if stripped == "!close":
        return {"type": "close"}

    # !<project> <message>
    m = re.match(r"^!(\w+)\s+([\s\S]+)", stripped)
    if m and m.group(1) in PROJECTS:
        return {"type": "message", "projectKey": m.group(1), "text": m.group(2).strip()}

    return {"type": "message", "projectKey": None, "text": stripped}


# ---------------------------------------------------------------------------
# Thread name builder
# ---------------------------------------------------------------------------

def build_thread_name(project_key: str, text: str) -> str:
    proj = PROJECTS.get(project_key, PROJECTS[DEFAULT_PROJECT])
    short = text[:80].replace("\n", " ")
    return f"{proj['emoji']} {short}"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_sessions(channel: discord.abc.Messageable) -> None:
    sessions = load_sessions()
    if not sessions:
        await channel.send("Active sessions: none")
        return

    from datetime import datetime, timezone
    lines = []
    for thread_id, s in sessions.items():
        proj = PROJECTS.get(s.get("projectKey"), PROJECTS[DEFAULT_PROJECT])
        last_used = datetime.fromisoformat(s["lastUsed"].replace("Z", "+00:00"))
        age = int((datetime.now(timezone.utc) - last_used).total_seconds() / 60)
        lines.append(
            f"{proj['emoji']} **{s['threadName']}** ({s['messageCount']} msgs, {age}min ago) <#{thread_id}>"
        )
    await send_long_message(channel, f"**Active Sessions:**\n" + "\n".join(lines))


async def handle_close(thread: discord.Thread) -> None:
    sessions = load_sessions()
    if str(thread.id) in sessions:
        del sessions[str(thread.id)]
        save_sessions(sessions)
    await thread.send("Session closed.")
    try:
        await thread.edit(archived=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Typing indicator context manager
# ---------------------------------------------------------------------------

class TypingLoop:
    """Send typing indicators every 5 seconds until stopped."""

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
# Main handler: new session via thread
# ---------------------------------------------------------------------------

async def handle_new_session(message: discord.Message, project_key: str, text: str) -> None:
    proj = PROJECTS[project_key]
    thread_name = build_thread_name(project_key, text)

    thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)

    typing = TypingLoop(thread)
    typing.start()

    try:
        result = await run_claude_code(proj["dir"], text, None)
        typing.stop()

        if result["sessionId"]:
            sessions = load_sessions()
            sessions[str(thread.id)] = {
                "sessionId": result["sessionId"],
                "projectKey": project_key,
                "projectDir": proj["dir"],
                "threadName": thread_name,
                "createdAt": _now_iso(),
                "lastUsed": _now_iso(),
                "messageCount": 1,
            }
            save_sessions(sessions)

        await send_long_message(thread, result["text"])
        print(f"[new] {thread_name} -> {len(result['text'])} chars")
    except Exception as e:
        typing.stop()
        print(f"[new] Error: {e}", file=sys.stderr)
        await thread.send(f"Error: {str(e)[:300]}")


# ---------------------------------------------------------------------------
# Main handler: continue session in thread
# ---------------------------------------------------------------------------

async def handle_thread_message(message: discord.Message) -> None:
    sessions = load_sessions()
    session = sessions.get(str(message.channel.id))

    if not session:
        await message.reply("This thread has no active session. Send a new message in the channel to start one.")
        return

    typing = TypingLoop(message.channel)
    typing.start()

    try:
        result = await run_claude_code(
            session["projectDir"],
            message.content.strip(),
            session["sessionId"],
        )
        typing.stop()

        if result["sessionId"]:
            session["sessionId"] = result["sessionId"]
        session["lastUsed"] = _now_iso()
        session["messageCount"] += 1
        save_sessions(sessions)

        await send_long_message(message.channel, result["text"])
        print(f"[cont] {session['threadName']} -> {len(result['text'])} chars (msg #{session['messageCount']})")
    except Exception as e:
        typing.stop()
        print(f"[cont] Error: {e}", file=sys.stderr)
        await message.channel.send(f"Error: {str(e)[:300]}")


# ---------------------------------------------------------------------------
# Main handler: DM fallback (one-shot, no session)
# ---------------------------------------------------------------------------

async def handle_dm(message: discord.Message) -> None:
    proj = PROJECTS[DEFAULT_PROJECT]

    typing = TypingLoop(message.channel)
    typing.start()

    try:
        result = await run_claude_code(proj["dir"], message.content.strip(), None)
        typing.stop()
        await send_long_message(message.channel, result["text"])
        print(f"[dm] {message.author} -> {len(result['text'])} chars")
    except Exception as e:
        typing.stop()
        print(f"[dm] Error: {e}", file=sys.stderr)
        await message.reply(f"Error: {str(e)[:300]}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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
    print(f"Logged in as {client.user}")
    print(f"Projects: {', '.join(PROJECTS.keys())}")
    print(f"Default: {DEFAULT_PROJECT}")

    # Pre-cache DM channels
    try:
        for guild in client.guilds:
            async for member in guild.fetch_members(limit=None):
                if not member.bot:
                    try:
                        await member.create_dm()
                    except Exception:
                        pass
        print("DM channels initialized")
    except Exception as e:
        print(f"DM init error: {e}", file=sys.stderr)


@client.event
async def on_message(message: discord.Message):
    # Debug: log ALL incoming messages
    author_tag = str(message.author) if message.author else "?"
    is_bot = message.author.bot if message.author else False
    ch_type = message.channel.type if message.channel else None
    content_preview = (message.content or "")[:50]
    print(f'[debug] msg from={author_tag} bot={is_bot} ch_type={ch_type} content="{content_preview}"')

    # Ignore bots
    if message.author.bot:
        return

    # Access control
    if ALLOWED_USERS and message.author.id not in ALLOWED_USERS:
        return

    is_dm = message.channel.type == ChannelType.private
    is_thread = message.channel.type in (ChannelType.public_thread, ChannelType.private_thread)
    is_guild_text = message.channel.type == ChannelType.text

    # Guild text channel -- only respond to mentions
    if is_guild_text and client.user not in message.mentions:
        cmd = parse_command(message.content)
        if cmd["type"] == "sessions":
            await handle_sessions(message.channel)
            return
        if not message.content.startswith("!"):
            return

    # Strip bot mention from content
    content = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()
    if not content:
        content = "hello"

    # Route by channel type
    if is_dm:
        await handle_dm(message)
        return

    if is_thread:
        cmd = parse_command(content)
        if cmd["type"] == "close":
            await handle_close(message.channel)
            return
        await handle_thread_message(message)
        return

    if is_guild_text:
        cmd = parse_command(content)
        if cmd["type"] == "sessions":
            await handle_sessions(message.channel)
            return
        project_key = cmd.get("projectKey") or DEFAULT_PROJECT
        text = cmd.get("text") or content
        await handle_new_session(message, project_key, text)
        return


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def main():
    print("Starting unified Discord bot (Python)...")
    # discord.py handles SIGINT/SIGTERM gracefully by default
    client.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
