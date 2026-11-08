"""Tests for the article-chat turn driver. The `claude` subprocess is a fake
runner; nothing here spawns the real CLI."""
import itertools
import json
import sqlite3
import subprocess

import pytest

from app import store
from app.chat import build_chat_command, chat_turn, sanitize_chat_html
from app.claude_cli import ClaudeError

ARTICLE_HTML = "<p>Rates stay high through 2027 because inflation is sticky.</p>"


def _ok(answer: str = "<p>A</p>"):
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps({"structured_output": {"answer_html": answer}}), stderr=""
    )


def _fail(returncode: int = 2, stderr: str = "boom"):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


_MISSING = _fail(1, "No conversation found with session ID: s1")


class FakeRunner:
    """Replays scripted results and records every call."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, cmd, stdin, timeout, cwd):
        self.calls.append({"cmd": cmd, "stdin": stdin, "timeout": timeout, "cwd": cwd})
        return self.results.pop(0) if self.results else _ok()


@pytest.fixture
def env(two_users):
    conn, alice, _bob = two_users
    conn.execute("INSERT INTO sources (key, type, title, folder) VALUES ('s1', 'rss', 'S', 'F')")
    source_id = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    conn.execute(
        """INSERT INTO articles (source_id, guid, url, title, published_at, origin, content_html)
           VALUES (?, 'g1', 'https://x/1', 'T', '2026-08-01T00:00:00Z', 'feed', ?)""",
        (source_id, ARTICLE_HTML),
    )
    conn.commit()
    article_id = conn.execute("SELECT id FROM articles").fetchone()[0]
    ids = (f"s{n}" for n in itertools.count(1))
    return conn, alice, article_id, lambda: next(ids)


def _turn(env, question, runner):
    conn, user, article_id, new_id = env
    return chat_turn(
        conn, user, article_id, ARTICLE_HTML, question, cwd="/data/claude-chat", timeout=123.0,
        runner=runner, new_id=new_id,
    )


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1]


@pytest.mark.parametrize("resume", [False, True])
def test_build_chat_command_flags(resume):
    cmd = build_chat_command("s1", resume=resume)
    assert cmd[:2] == ["claude", "-p"]
    assert "--bare" not in cmd
    assert "--no-session-persistence" not in cmd
    assert _flag(cmd, "--tools") == ""
    assert _flag(cmd, "--setting-sources") == ""
    assert "--strict-mcp-config" in cmd
    assert _flag(cmd, "--model") == "sonnet"
    if resume:
        assert _flag(cmd, "--resume") == "s1" and "--session-id" not in cmd
    else:
        assert _flag(cmd, "--session-id") == "s1" and "--resume" not in cmd


def test_first_turn_sends_article_and_question(env):
    runner = FakeRunner()
    saved = _turn(env, "Why?", runner)

    call = runner.calls[0]
    assert "inflation is sticky" in call["stdin"]
    assert "Question: Why?" in call["stdin"]
    assert call["cwd"] == "/data/claude-chat"
    assert call["timeout"] == 123.0
    assert _flag(call["cmd"], "--session-id") == "s1"
    assert [m["role"] for m in saved] == ["user", "assistant"]
    conn, user, article_id, _ = env
    assert store.get_chat_session(conn, user, article_id)["session_id"] == "s1"


def test_second_turn_resumes_with_question_only(env):
    runner = FakeRunner()
    _turn(env, "Why?", runner)
    _turn(env, "And then?", runner)

    call = runner.calls[1]
    assert call["stdin"] == "And then?"
    assert _flag(call["cmd"], "--resume") == "s1"


def test_resume_missing_reseeds_with_history(env):
    runner = FakeRunner(_ok("<p>First answer</p>"), _MISSING, _ok("<p>Second</p>"))
    _turn(env, "Why?", runner)
    saved = _turn(env, "And then?", runner)

    reseed = runner.calls[2]
    assert _flag(reseed["cmd"], "--session-id") == "s2"
    assert "inflation is sticky" in reseed["stdin"]
    assert "Earlier conversation:" in reseed["stdin"]
    assert "User: Why?" in reseed["stdin"]
    assert "Assistant: First answer" in reseed["stdin"]
    assert "And then?" in reseed["stdin"]
    assert saved[1]["body"] == "<p>Second</p>"
    conn, user, article_id, _ = env
    assert store.get_chat_session(conn, user, article_id)["session_id"] == "s2"
    assert len(store.get_chat_messages(conn, user, article_id)) == 4


def test_failed_resume_marks_reseed_and_saves_nothing(env):
    runner = FakeRunner(_ok(), _fail())
    _turn(env, "Why?", runner)
    with pytest.raises(ClaudeError):
        _turn(env, "And then?", runner)

    conn, user, article_id, _ = env
    assert len(store.get_chat_messages(conn, user, article_id)) == 2
    assert store.get_chat_session(conn, user, article_id)["needs_reseed"] is True

    _turn(env, "And then?", runner)
    retry = runner.calls[2]
    assert _flag(retry["cmd"], "--session-id") == "s2"
    assert "Earlier conversation:" in retry["stdin"]


def test_failed_first_turn_saves_nothing_and_retry_uses_new_id(env):
    runner = FakeRunner(_fail())
    with pytest.raises(ClaudeError):
        _turn(env, "Why?", runner)

    conn, user, article_id, _ = env
    assert store.get_chat_messages(conn, user, article_id) == []
    assert store.get_chat_session(conn, user, article_id) is None

    _turn(env, "Why?", runner)
    assert _flag(runner.calls[1]["cmd"], "--session-id") == "s2"
    assert "Earlier conversation:" not in runner.calls[1]["stdin"]


def test_answer_is_sanitized_before_saving(env):
    runner = FakeRunner(_ok('<p class="beyond">x</p><script>alert(1)</script>'))
    saved = _turn(env, "Why?", runner)
    assert "<script>" not in saved[1]["body"]
    assert 'class="beyond"' in saved[1]["body"]


def test_sanitize_chat_html_keeps_only_beyond_class():
    out = sanitize_chat_html(
        '<p class="beyond x"><b>Beyond the article:</b> y</p>'
        '<p class="evil" onclick="z">q</p><script>1</script><a href="u">l</a>'
    )
    assert '<p class="beyond"><b>Beyond the article:</b> y</p>' in out
    assert "evil" not in out
    assert "onclick" not in out
    assert "<script" not in out
    assert "<a" not in out


def test_save_failure_marks_reseed_and_raises_claude_error(env, monkeypatch):
    # The CLI recorded the turn but SQLite didn't: the next turn must not
    # resume a session holding a turn the user can't see.
    runner = FakeRunner()
    _turn(env, "Why?", runner)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "save_chat_turn", locked)
    with pytest.raises(ClaudeError):
        _turn(env, "And then?", runner)
    monkeypatch.undo()

    conn, user, article_id, _ = env
    assert store.get_chat_session(conn, user, article_id)["needs_reseed"] is True
    assert len(store.get_chat_messages(conn, user, article_id)) == 2
