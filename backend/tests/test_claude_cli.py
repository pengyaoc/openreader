"""Tests for the shared `claude` CLI helper: env scrubbing, envelope parsing,
the missing-session signal, and the process-wide one-at-a-time slot."""
import json
import subprocess
import threading
import time

import pytest

from app import claude_cli
from app.claude_cli import (
    ClaudeBusyError,
    ClaudeError,
    ClaudeSlot,
    SessionMissingError,
    default_runner,
    run_structured,
)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_default_runner_scrubs_api_key(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _completed("{}")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    default_runner(["claude"], "hi", 5.0, "/some/dir")
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert captured["cwd"] == "/some/dir"
    assert captured["input"] == "hi"


def test_run_structured_maps_missing_session():
    def runner(cmd, stdin, timeout, cwd):
        return _completed(returncode=1, stderr="No conversation found with session ID: abc")

    with pytest.raises(SessionMissingError):
        run_structured(["claude"], "q", 5.0, None, runner, key="answer_html")


def _raise_timeout(cmd, stdin, timeout, cwd):
    raise subprocess.TimeoutExpired(cmd, timeout)


@pytest.mark.parametrize(
    "runner",
    [
        lambda c, s, t, d: _completed(returncode=2, stderr="boom"),
        lambda c, s, t, d: _completed("not json"),
        lambda c, s, t, d: _completed(json.dumps({"is_error": True, "result": "x"})),
        lambda c, s, t, d: _completed(json.dumps({"structured_output": {}})),
        _raise_timeout,
    ],
)
def test_run_structured_other_failures_raise_claude_error(runner):
    with pytest.raises(ClaudeError) as info:
        run_structured(["claude"], "q", 5.0, None, runner, key="answer_html")
    assert not isinstance(info.value, SessionMissingError)


def test_run_structured_returns_key():
    def runner(cmd, stdin, timeout, cwd):
        return _completed(json.dumps({"structured_output": {"answer_html": "<p>a</p>"}}))

    assert run_structured(["claude"], "q", 5.0, None, runner, key="answer_html") == "<p>a</p>"


def test_slot_serializes_and_rejects_third_waiter():
    slot = ClaudeSlot(max_waiting=2)
    release_a = threading.Event()
    a_in = threading.Event()
    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    entered = []

    def holder():
        with slot.acquire(5):
            a_in.set()
            release_a.wait(5)

    def waiter(name):
        nonlocal active, max_active
        with slot.acquire(5):
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            entered.append(name)
            with counter_lock:
                active -= 1

    a = threading.Thread(target=holder)
    a.start()
    assert a_in.wait(2)
    b = threading.Thread(target=waiter, args=("b",))
    c = threading.Thread(target=waiter, args=("c",))
    b.start()
    c.start()
    deadline = time.monotonic() + 2
    while slot.waiting < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert slot.waiting == 2

    with pytest.raises(ClaudeBusyError):
        with slot.acquire(5):
            pass

    release_a.set()
    for t in (a, b, c):
        t.join(5)
    assert sorted(entered) == ["b", "c"]
    assert max_active == 1


def test_slot_yields_remaining_deadline():
    slot = ClaudeSlot()
    held = threading.Event()

    def holder():
        with slot.acquire(5):
            held.set()
            time.sleep(0.2)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(2)
    with slot.acquire(1.0) as remaining:
        assert 0.5 < remaining < 1.0
    t.join()


def test_slot_wait_timeout():
    slot = ClaudeSlot()
    held = threading.Event()
    release = threading.Event()

    def holder():
        with slot.acquire(5):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(2)
    with pytest.raises(ClaudeError) as info:
        with slot.acquire(0.1):
            pass
    assert not isinstance(info.value, ClaudeBusyError)
    release.set()
    t.join()
