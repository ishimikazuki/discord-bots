import asyncio
import copy
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


def import_bot_for_tests(monkeypatch):
    """Import bot.py without depending on the developer's keychain in CI-like runs."""
    monkeypatch.setattr(sys, "argv", ["bot.py", "kanojo"])

    real_run = subprocess.run

    def fake_run(args, *pargs, **kwargs):
        if args and args[0] == "security":
            return subprocess.CompletedProcess(args, 0, stdout="test-token\n", stderr="")
        return real_run(args, *pargs, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    if "bot" in sys.modules:
        return sys.modules["bot"]

    import importlib

    return importlib.import_module("bot")


@pytest.fixture
def bot_module(monkeypatch):
    bot = import_bot_for_tests(monkeypatch)
    monkeypatch.setattr(bot, "BOT_NAME", "kanojo")
    return bot


class FakeByteReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def readline(self):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    async def read(self, _size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class FakeProcess:
    def __init__(self, stdout_events, *, returncode=0, stderr=b""):
        lines = [json.dumps(event).encode() + b"\n" for event in stdout_events]
        self.stdout = FakeByteReader(lines)
        self.stderr = FakeByteReader([stderr] if stderr else [])
        self.stdin = FakeStdin()
        self.returncode = returncode
        self.killed = False

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class NoopTyping:
    def __init__(self, _channel):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def test_build_codex_args_uses_codex_exec_and_compat_project_doc(bot_module):
    new_args = bot_module.build_codex_args(None)
    resume_args = bot_module.build_codex_args("codex-thread-123")

    assert new_args[:2] == ["codex", "exec"]
    assert new_args[-1] == "-"
    assert "--json" in new_args
    assert "--dangerously-bypass-approvals-and-sandbox" in new_args
    assert 'project_doc_fallback_filenames=["CLAUDE.md"]' in new_args

    assert resume_args[:3] == ["codex", "exec", "resume"]
    assert resume_args[-2:] == ["codex-thread-123", "-"]
    assert "claude --" not in " ".join(new_args + resume_args).lower()


@pytest.mark.asyncio
async def test_run_codex_code_parses_jsonl_and_feeds_prompt(bot_module, monkeypatch, tmp_path):
    proc = FakeProcess(
        [
            {"type": "thread.started", "thread_id": "codex-thread-abc"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "カード要約OK"},
            },
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        ]
    )
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(bot_module.shutil, "which", lambda *_args, **_kwargs: "/fake/bin/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await bot_module._run_codex_code_unlocked(
        str(tmp_path), "カード明細を読んで", None
    )

    assert captured["args"][0:2] == ("/fake/bin/codex", "exec")
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert proc.stdin.data.decode() == "カード明細を読んで"
    assert result["text"] == "カード要約OK"
    assert result["sessionId"] == "codex-thread-abc"
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}


@pytest.mark.asyncio
async def test_run_codex_code_resume_keeps_existing_session_without_thread_started(
    bot_module, monkeypatch, tmp_path
):
    proc = FakeProcess(
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "続きOK"},
            },
        ]
    )

    async def fake_create_subprocess_exec(*args, **_kwargs):
        assert args[:3] == ("/fake/bin/codex", "exec", "resume")
        assert args[-2:] == ("codex-thread-existing", "-")
        return proc

    monkeypatch.setattr(bot_module.shutil, "which", lambda *_args, **_kwargs: "/fake/bin/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await bot_module._run_codex_code_unlocked(
        str(tmp_path), "続きお願い", "codex-thread-existing"
    )

    assert result["text"] == "続きOK"
    assert result["sessionId"] == "codex-thread-existing"


@pytest.mark.asyncio
async def test_run_codex_code_failure_reports_codex_event_message(bot_module, monkeypatch, tmp_path):
    proc = FakeProcess(
        [{"type": "error", "message": "Codex auth failed"}],
        returncode=1,
    )

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(bot_module.shutil, "which", lambda *_args, **_kwargs: "/fake/bin/codex")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="Codex auth failed"):
        await bot_module._run_codex_code_unlocked(str(tmp_path), "hello", None)


def test_runtime_files_do_not_call_claude_cli():
    root = Path(__file__).resolve().parents[1]
    runtime_files = [
        "bot.py",
        "bot.js",
        "config.json",
        "launchd/generate-plists.sh",
        "setup-macmini.sh",
        "start-bots.command",
        "card_summary/scheduler.py",
    ]

    for rel in runtime_files:
        text = (root / rel).read_text(encoding="utf-8")
        normalized = text.lower().replace("claude.md", "")
        assert "claude --" not in normalized, rel
        assert "claude_errors" not in normalized, rel
        assert "claude_timeout" not in normalized, rel
        assert "claude_max" not in normalized, rel


def test_prompt_contract_replays_historical_schedule_and_card_requests(bot_module):
    root = Path(__file__).resolve().parents[1]
    sessions_file = root / "sessions-kanojo.json"
    if not sessions_file.exists():
        pytest.skip("requires local ignored sessions-kanojo.json history")
    sessions = json.loads(sessions_file.read_text(encoding="utf-8"))
    titles = [session["threadName"] for session in sessions.values()]
    schedule_like_titles = [
        title
        for title in titles
        if any(keyword in title for keyword in ("予定", "Google Meet", "📍", "カレンダー"))
    ]

    assert schedule_like_titles, "historical kanojo sessions should include schedule-like chats"

    replay_inputs = schedule_like_titles[:5] + [
        "このカード利用サマリーについて、食費だけ内訳を教えて",
        "不動産営業のPOCをする予定を、明日4時間入れておいてください。",
    ]

    for user_text in replay_inputs:
        prompt = bot_module.build_prompt_with_inbox(user_text, [])
        assert "[Discord連携ルール]" in prompt
        assert "_inbox/" in prompt
        assert "_outbox/" in prompt
        assert user_text in prompt
        assert "Claude Code" not in prompt


@pytest.mark.asyncio
async def test_legacy_claude_session_is_warned_and_not_resumed(bot_module, monkeypatch):
    channel = SimpleNamespace(id=12345, sent=[])

    async def send(content=None, **_kwargs):
        channel.sent.append(content)

    channel.send = send
    message = SimpleNamespace(channel=channel, content="続きやって", attachments=[])
    sessions = {
        str(channel.id): {
            "sessionId": "old-claude-session",
            "agent": "claude",
            "projectDir": "/tmp/project",
            "workDir": "/tmp/project",
            "threadName": "old thread",
            "messageCount": 1,
        }
    }

    async def fail_run_codex(*_args, **_kwargs):
        raise AssertionError("legacy Claude sessions must not be resumed with Codex")

    monkeypatch.setattr(bot_module, "load_sessions", lambda: sessions)
    monkeypatch.setattr(bot_module, "run_codex_code", fail_run_codex)

    await bot_module._handle_thread_message_locked(message)

    assert channel.sent == [
        "このスレッドは旧AIセッションなので、Codexでは再開できません。"
        "新しいスレッドで始め直してください。"
    ]


@pytest.mark.asyncio
async def test_card_summary_thread_context_is_injected_into_first_codex_turn(
    bot_module, monkeypatch, tmp_path
):
    context_file = tmp_path / "card-summary.txt"
    context_file.write_text("5月カード合計: 41,815円\n食費: 12,000円\n", encoding="utf-8")
    channel = SimpleNamespace(id=67890, sent=[])

    async def send(content=None, **_kwargs):
        channel.sent.append(content)

    channel.send = send
    message = SimpleNamespace(
        channel=channel,
        content="このカード利用サマリーの食費だけ教えて",
        attachments=[],
    )
    sessions = {
        str(channel.id): {
            "sessionId": None,
            "agent": "codex",
            "projectDir": str(tmp_path),
            "workDir": str(tmp_path),
            "threadName": "カードサマリー",
            "messageCount": 0,
            "kanojo_context_file": str(context_file),
        }
    }
    codex_calls = []
    saved_sessions = []

    async def fake_run_codex(work_dir, prompt, session_id):
        codex_calls.append((work_dir, prompt, session_id))
        return {
            "text": "食費は12,000円だよ。",
            "sessionId": "codex-card-thread",
            "cost": 0,
            "usage": {},
        }

    async def fake_save_inbox(*_args, **_kwargs):
        return []

    async def fake_send_outbox(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(bot_module, "load_sessions", lambda: sessions)
    monkeypatch.setattr(bot_module, "save_sessions", lambda data: saved_sessions.append(copy.deepcopy(data)))
    monkeypatch.setattr(bot_module, "save_inbox_attachments", fake_save_inbox)
    monkeypatch.setattr(bot_module, "run_codex_code", fake_run_codex)
    monkeypatch.setattr(bot_module, "send_outbox_files", fake_send_outbox)
    monkeypatch.setattr(bot_module, "TypingLoop", NoopTyping)

    await bot_module._handle_thread_message_locked(message)

    assert channel.sent == ["食費は12,000円だよ。"]
    assert len(codex_calls) == 1
    work_dir, prompt, session_id = codex_calls[0]
    assert work_dir == str(tmp_path)
    assert session_id is None
    assert "<background>" in prompt
    assert "5月カード合計: 41,815円" in prompt
    assert "ユーザーの質問: このカード利用サマリーの食費だけ教えて" in prompt
    assert "[Discord連携ルール]" in prompt
    assert saved_sessions[-1][str(channel.id)]["sessionId"] == "codex-card-thread"
    assert saved_sessions[-1][str(channel.id)]["agent"] == "codex"
    assert saved_sessions[-1][str(channel.id)]["messageCount"] == 1


@pytest.mark.asyncio
async def test_continuation_persists_recoverable_pending_prompt_before_codex_runs(
    bot_module, monkeypatch, tmp_path
):
    sessions_path = tmp_path / "sessions-kanojo.json"
    sessions_path.write_text(json.dumps({
        "333": {
            "sessionId": "codex-existing",
            "agent": "codex",
            "projectDir": str(tmp_path),
            "workDir": str(tmp_path),
            "threadName": "💕 続きテスト",
            "lastUsed": "2026-05-09T00:00:00+00:00",
            "messageCount": 1,
        }
    }), encoding="utf-8")
    monkeypatch.setattr(bot_module, "SESSIONS_FILE", sessions_path)
    monkeypatch.setattr(bot_module, "TypingLoop", NoopTyping)

    channel = SimpleNamespace(id=333, sent=[])

    async def send(content=None, **_kwargs):
        channel.sent.append(content)

    channel.send = send
    message = SimpleNamespace(id=444, channel=channel, content="おーい", attachments=[])

    async def fake_save_inbox(*_args, **_kwargs):
        return []

    async def fake_send_outbox(*_args, **_kwargs):
        return 0

    async def fake_run_codex(work_dir, prompt, session_id):
        saved = json.loads(sessions_path.read_text(encoding="utf-8"))
        pending = saved["333"]
        assert pending["pending"] is True
        assert pending["pendingUserText"] == "おーい"
        assert pending["pendingSourceMessageId"] == "444"
        assert "おーい" in pending["pendingPrompt"]
        assert work_dir == str(tmp_path)
        assert prompt == pending["pendingPrompt"]
        assert session_id == "codex-existing"
        return {"text": "いるよ", "sessionId": "codex-existing", "cost": 0, "usage": {}}

    monkeypatch.setattr(bot_module, "save_inbox_attachments", fake_save_inbox)
    monkeypatch.setattr(bot_module, "send_outbox_files", fake_send_outbox)
    monkeypatch.setattr(bot_module, "run_codex_code", fake_run_codex)

    await bot_module._handle_thread_message_locked(message)

    saved = json.loads(sessions_path.read_text(encoding="utf-8"))
    session = saved["333"]
    assert channel.sent == ["いるよ"]
    assert session["messageCount"] == 2
    assert "pending" not in session
    assert "pendingPrompt" not in session


@pytest.mark.asyncio
async def test_new_session_persists_recoverable_pending_prompt_before_codex_runs(
    bot_module, monkeypatch, tmp_path
):
    sessions_path = tmp_path / "sessions-kanojo.json"
    project_dir = tmp_path / "project"
    worktree_dir = project_dir / ".worktrees" / "thread-4242"
    project_dir.mkdir()
    worktree_dir.mkdir(parents=True)
    monkeypatch.setattr(bot_module, "SESSIONS_FILE", sessions_path)
    monkeypatch.setattr(bot_module, "PROJECT_DIR", str(project_dir))
    monkeypatch.setattr(bot_module, "AUTO_PULL", False)
    monkeypatch.setattr(bot_module, "create_worktree", lambda *_args: str(worktree_dir))
    monkeypatch.setattr(bot_module, "TypingLoop", NoopTyping)

    class Thread:
        id = 4242

        def __init__(self):
            self.sent = []

        async def send(self, content=None, **_kwargs):
            self.sent.append(content)

    thread = Thread()

    async def fake_save_inbox(*_args, **_kwargs):
        return []

    async def fake_send_outbox(*_args, **_kwargs):
        return 0

    async def fake_run_codex(work_dir, prompt, session_id):
        saved = json.loads(sessions_path.read_text(encoding="utf-8"))
        pending = saved[str(thread.id)]
        assert pending["pending"] is True
        assert pending["pendingUserText"] == "昨日の日記を書いて"
        assert "昨日の日記を書いて" in pending["pendingPrompt"]
        assert pending["workDir"] == str(worktree_dir)
        assert pending["worktreePath"] == str(worktree_dir)
        assert work_dir == str(worktree_dir)
        assert prompt == pending["pendingPrompt"]
        assert session_id is None
        return {"text": "書いたよ", "sessionId": "codex-new", "cost": 0, "usage": {}}

    monkeypatch.setattr(bot_module, "save_inbox_attachments", fake_save_inbox)
    monkeypatch.setattr(bot_module, "send_outbox_files", fake_send_outbox)
    monkeypatch.setattr(bot_module, "run_codex_code", fake_run_codex)

    await bot_module._start_session_locked(
        thread,
        "昨日の日記を書いて",
        "💕 昨日の日記を書いて",
        trigger_message=SimpleNamespace(id=999, attachments=[]),
    )

    saved = json.loads(sessions_path.read_text(encoding="utf-8"))
    session = saved[str(thread.id)]
    assert session["sessionId"] == "codex-new"
    assert "pending" not in session
    assert "pendingPrompt" not in session
    assert thread.sent == ["書いたよ"]


@pytest.mark.asyncio
async def test_recover_pending_session_with_prompt_replays_codex_turn(
    bot_module, monkeypatch, tmp_path
):
    sessions_path = tmp_path / "sessions-kanojo.json"
    sessions_path.write_text(json.dumps({
        "111": {
            "sessionId": "codex-existing",
            "agent": "codex",
            "projectDir": str(tmp_path),
            "workDir": str(tmp_path),
            "threadName": "💕 復旧テスト",
            "messageCount": 1,
            "pending": True,
            "pendingPrompt": "[ユーザーの依頼]\n復旧して",
        }
    }), encoding="utf-8")
    monkeypatch.setattr(bot_module, "SESSIONS_FILE", sessions_path)
    monkeypatch.setattr(bot_module, "TypingLoop", NoopTyping)

    channel = SimpleNamespace(id=111, sent=[])

    async def send(content=None, **_kwargs):
        channel.sent.append(content)

    channel.send = send

    class FakeClient:
        def get_channel(self, channel_id):
            return channel if channel_id == 111 else None

    async def fake_run_codex(work_dir, prompt, session_id):
        assert work_dir == str(tmp_path)
        assert prompt == "[ユーザーの依頼]\n復旧して"
        assert session_id == "codex-existing"
        return {"text": "復旧したよ", "sessionId": "codex-recovered", "cost": 0, "usage": {}}

    async def fake_send_outbox(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(bot_module, "client", FakeClient())
    monkeypatch.setattr(bot_module, "run_codex_code", fake_run_codex)
    monkeypatch.setattr(bot_module, "send_outbox_files", fake_send_outbox)

    await bot_module.recover_pending_sessions()

    saved = json.loads(sessions_path.read_text(encoding="utf-8"))
    session = saved["111"]
    assert channel.sent == ["復旧したよ"]
    assert session["sessionId"] == "codex-recovered"
    assert session["messageCount"] == 2
    assert "pending" not in session
    assert "pendingPrompt" not in session


@pytest.mark.asyncio
async def test_recover_pending_session_without_prompt_asks_user_to_resend(
    bot_module, monkeypatch, tmp_path
):
    sessions_path = tmp_path / "sessions-kanojo.json"
    sessions_path.write_text(json.dumps({
        "222": {
            "sessionId": None,
            "agent": "codex",
            "projectDir": str(tmp_path),
            "workDir": str(tmp_path),
            "threadName": "💕 再起動で止まった",
            "messageCount": 0,
            "pending": True,
        }
    }), encoding="utf-8")
    monkeypatch.setattr(bot_module, "SESSIONS_FILE", sessions_path)

    channel = SimpleNamespace(id=222, sent=[])

    async def send(content=None, **_kwargs):
        channel.sent.append(content)

    channel.send = send

    class FakeClient:
        def get_channel(self, channel_id):
            return channel if channel_id == 222 else None

    async def fail_run_codex(*_args, **_kwargs):
        raise AssertionError("missing prompts should not be sent to Codex")

    monkeypatch.setattr(bot_module, "client", FakeClient())
    monkeypatch.setattr(bot_module, "run_codex_code", fail_run_codex)

    await bot_module.recover_pending_sessions()

    saved = json.loads(sessions_path.read_text(encoding="utf-8"))
    session = saved["222"]
    assert len(channel.sent) == 1
    assert "もう一度送って" in channel.sent[0]
    assert "pending" not in session
    assert session["lastError"] == "pending prompt missing after bot restart"


@pytest.mark.asyncio
async def test_backfill_missed_thread_messages_replays_user_post_after_disconnect(
    bot_module, monkeypatch, tmp_path
):
    sessions_path = tmp_path / "sessions-kanojo.json"
    context_file = tmp_path / "card-summary.txt"
    context_file.write_text("カードサマリー本文", encoding="utf-8")
    sessions_path.write_text(json.dumps({
        "1515113471082234047": {
            "sessionId": None,
            "agent": "codex",
            "projectDir": str(tmp_path),
            "workDir": str(tmp_path),
            "threadName": "🔔 6/13 7:00",
            "lastUsed": "2026-06-12T22:00:01+00:00",
            "messageCount": 0,
            "kanojo_context_file": str(context_file),
        }
    }), encoding="utf-8")
    monkeypatch.setattr(bot_module, "SESSIONS_FILE", sessions_path)
    monkeypatch.setattr(bot_module, "TypingLoop", NoopTyping)
    monkeypatch.setattr(bot_module, "ALLOWED_USERS", [123])

    class FakeAuthor:
        bot = False
        id = 123

    class FakeMessage:
        def __init__(self):
            self.id = 1515191215438692505
            self.author = FakeAuthor()
            self.content = "それはエラーやろ。サマリーじゃなくてエラーとして吐き出せよ"
            self.attachments = []
            self.created_at = bot_module.datetime.fromisoformat("2026-06-13T03:08:56+00:00")

    class FakeChannel:
        id = 1515113471082234047

        def __init__(self):
            self.sent = []
            self._messages = []
            message = FakeMessage()
            message.channel = self
            self._messages.append(message)

        async def history(self, **kwargs):
            assert kwargs["oldest_first"] is True
            for message in self._messages:
                yield message

        async def send(self, content=None, **_kwargs):
            self.sent.append(content)

    channel = FakeChannel()

    class FakeClient:
        def get_channel(self, channel_id):
            return channel if channel_id == channel.id else None

    codex_calls = []

    async def fake_run_codex(work_dir, prompt, session_id):
        codex_calls.append((work_dir, prompt, session_id))
        return {
            "text": "次からエラーとして出すね",
            "sessionId": "codex-backfilled",
            "cost": 0,
            "usage": {},
        }

    async def fake_save_inbox(*_args, **_kwargs):
        return []

    async def fake_send_outbox(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(bot_module, "client", FakeClient())
    monkeypatch.setattr(bot_module, "run_codex_code", fake_run_codex)
    monkeypatch.setattr(bot_module, "save_inbox_attachments", fake_save_inbox)
    monkeypatch.setattr(bot_module, "send_outbox_files", fake_send_outbox)

    await bot_module.backfill_missed_thread_messages()

    saved = json.loads(sessions_path.read_text(encoding="utf-8"))
    session = saved[str(channel.id)]
    assert channel.sent == ["次からエラーとして出すね"]
    assert len(codex_calls) == 1
    assert codex_calls[0][2] is None
    assert "カードサマリー本文" in codex_calls[0][1]
    assert "それはエラーやろ" in codex_calls[0][1]
    assert session["sessionId"] == "codex-backfilled"
    assert session["messageCount"] == 1
    assert session["lastProcessedMessageId"] == "1515191215438692505"


@pytest.mark.asyncio
async def test_backfill_missed_thread_messages_skips_stale_history(
    bot_module, monkeypatch, tmp_path
):
    sessions_path = tmp_path / "sessions-kanojo.json"
    stale_last_used = bot_module.datetime.now(bot_module.timezone.utc) - timedelta(days=3)
    stale_message_time = bot_module.datetime.now(bot_module.timezone.utc) - timedelta(days=2)
    sessions_path.write_text(json.dumps({
        "1509849324765577358": {
            "sessionId": "codex-old",
            "agent": "codex",
            "projectDir": str(tmp_path),
            "workDir": str(tmp_path),
            "threadName": "古いカード要約",
            "lastUsed": stale_last_used.isoformat(),
            "messageCount": 1,
        }
    }), encoding="utf-8")
    monkeypatch.setattr(bot_module, "SESSIONS_FILE", sessions_path)
    monkeypatch.setattr(bot_module, "ALLOWED_USERS", [123])
    bot_module._processed_messages.clear()
    monkeypatch.setattr(bot_module, "_backfill_running", False)
    monkeypatch.setattr(bot_module, "_last_backfill_started_at", None)

    class FakeAuthor:
        bot = False
        id = 123

    class FakeMessage:
        def __init__(self):
            self.id = 1510334509462196396
            self.author = FakeAuthor()
            self.content = "古い未処理投稿"
            self.attachments = []
            self.created_at = stale_message_time

    class FakeChannel:
        id = 1509849324765577358

        def __init__(self):
            message = FakeMessage()
            message.channel = self
            self._messages = [message]

        async def history(self, **kwargs):
            assert kwargs["oldest_first"] is True
            for message in self._messages:
                yield message

    class FakeClient:
        def get_channel(self, channel_id):
            return FakeChannel() if channel_id == 1509849324765577358 else None

    codex_calls = []

    async def fake_run_codex(*args):
        codex_calls.append(args)
        return {
            "text": "処理しない",
            "sessionId": "codex-should-not-run",
            "cost": 0,
            "usage": {},
        }

    monkeypatch.setattr(bot_module, "client", FakeClient())
    monkeypatch.setattr(bot_module, "run_codex_code", fake_run_codex)

    await bot_module.backfill_missed_thread_messages()

    saved = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert codex_calls == []
    assert saved["1509849324765577358"]["sessionId"] == "codex-old"
    assert "lastProcessedMessageId" not in saved["1509849324765577358"]


@pytest.mark.asyncio
async def test_card_summary_registers_new_threads_as_codex_sessions(monkeypatch, tmp_path):
    from card_summary import scheduler

    sessions_path = tmp_path / "sessions-kanojo.json"
    context_dir = tmp_path / "contexts"
    monkeypatch.setattr(scheduler, "CONTEXT_DIR", context_dir)

    thread = SimpleNamespace(id=555111, name="5/9 22:00 カード要約")
    await scheduler.register_kanojo_session(
        sessions_path,
        thread,
        "night",
        "カード合計: 41,815円",
        "/Users/kazuki-macmini/kanojo",
    )

    data = json.loads(sessions_path.read_text(encoding="utf-8"))
    session = data[str(thread.id)]
    assert session["sessionId"] is None
    assert session["agent"] == "codex"
    assert session["projectDir"] == "/Users/kazuki-macmini/kanojo"
    assert session["workDir"] == "/Users/kazuki-macmini/kanojo"
    assert session["kanojo_slot"] == "night"
    assert Path(session["kanojo_context_file"]).read_text(encoding="utf-8") == "カード合計: 41,815円"


def test_kanojo_calendar_skill_is_visible_to_codex_and_tracked_for_new_worktrees():
    project = Path.home() / "kanojo"
    if not project.exists():
        pytest.skip("requires local ~/kanojo project checkout")
    skill_link = project / ".agents" / "skills"
    skill = skill_link / "screenshot-to-calendar" / "SKILL.md"

    assert (project / "CLAUDE.md").is_file()
    assert (project / "core" / "calendar_client.py").is_file()
    assert (project / "core" / "manual_event.py").is_file()
    assert (project / "tests" / "test_calendar_client.py").is_file()

    assert skill_link.is_symlink()
    assert skill_link.resolve() == (project / ".claude" / "skills").resolve()
    assert skill.is_file()

    skill_text = skill.read_text(encoding="utf-8")
    assert "Codex" in skill_text
    assert "Claude Code" not in skill_text
    assert "insert_manual_event" in skill_text
    assert "find_duplicate_event" in skill_text
    assert "登録前に必ず Discord で確認" in skill_text

    tracked = subprocess.run(
        ["git", "cat-file", "-p", "HEAD:.agents/skills"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0
    assert tracked.stdout.strip() == "../.claude/skills"
