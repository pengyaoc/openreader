"""Shared plumbing for every `claude` CLI call the reader makes (Summarize
and article chat).

Runs against the Claude subscription (OAuth keychain), not the API key —
two rules make that true and must never be relaxed:
  1. Never pass --bare (under --bare, auth is strictly ANTHROPIC_API_KEY or
     apiKeyHelper; OAuth and the keychain are never read — that would
     silently bill the API).
  2. Scrub ANTHROPIC_API_KEY from the child environment (default_runner).

BASE_FLAGS keep each call small and deterministic: no tools, no MCP
servers, no settings/CLAUDE.md/hooks, no slash commands.

CLAUDE_SLOT keeps at most one `claude` process alive at a time. One call
peaks around 300MB RSS and openreader.service runs under MemoryMax=700M on
a 1GB VM (docs/WORKLOG.md, 2026-08-14), so two at once risks the OOM
killer. At most two requests may wait for the slot; a third is told the
reader is busy rather than queueing indefinitely.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class ClaudeError(Exception):
    """Any failure of a `claude` call — non-zero exit, timeout, malformed
    output, or a reported model-side error."""


class ClaudeBusyError(ClaudeError):
    """Too many requests already waiting for the one-at-a-time slot."""


class SessionMissingError(ClaudeError):
    """`--resume` named a session the CLI no longer has on disk."""


Runner = Callable[[list[str], str, float, "str | None"], subprocess.CompletedProcess]

BASE_FLAGS: list[str] = [
    "--tools",
    "",
    "--strict-mcp-config",
    "--setting-sources",
    "",
    "--disable-slash-commands",
    "--output-format",
    "json",
]

DEADLINE_S = 300.0

# Verified against CLI 2.1.303: exit 1, this text on stderr.
_SESSION_MISSING_MARKER = "No conversation found with session ID"


def default_runner(
    cmd: list[str], stdin_text: str, timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    return subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd
    )


def run_structured(
    cmd: list[str], stdin_text: str, timeout: float, cwd: str | None, runner: Runner, key: str
) -> str:
    """Runs one `claude -p --output-format json --json-schema ...` call and
    returns structured_output[key]. Every failure becomes a ClaudeError;
    a resume of a missing session becomes SessionMissingError."""
    try:
        result = runner(cmd, stdin_text, timeout, cwd)
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"claude timed out after {timeout:.0f}s") from exc

    if result.returncode != 0:
        if _SESSION_MISSING_MARKER in (result.stderr or ""):
            raise SessionMissingError(result.stderr.strip()[:500])
        raise ClaudeError(f"claude exited {result.returncode}: {(result.stderr or '')[:500]}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"could not parse claude output as JSON: {exc}") from exc

    if envelope.get("is_error"):
        raise ClaudeError(f"claude reported an error: {envelope.get('result')}")

    structured = envelope.get("structured_output")
    if not isinstance(structured, dict) or not isinstance(structured.get(key), str):
        raise ClaudeError(f"claude output missing structured_output.{key}")
    return structured[key]


class ClaudeSlot:
    """A lock with a bounded waiting line and a deadline that includes the
    time spent waiting."""

    def __init__(self, max_waiting: int = 2) -> None:
        self._lock = threading.Lock()
        self._count_lock = threading.Lock()
        self._max_waiting = max_waiting
        self.waiting = 0

    @contextmanager
    def acquire(self, deadline_s: float) -> Iterator[float]:
        start = time.monotonic()
        if not self._lock.acquire(blocking=False):
            with self._count_lock:
                if self.waiting >= self._max_waiting:
                    raise ClaudeBusyError("busy, try again")
                self.waiting += 1
            try:
                got = self._lock.acquire(timeout=deadline_s)
            finally:
                with self._count_lock:
                    self.waiting -= 1
            if not got:
                raise ClaudeError(f"timed out after {deadline_s:.0f}s waiting for another Claude request")
        try:
            yield deadline_s - (time.monotonic() - start)
        finally:
            self._lock.release()


CLAUDE_SLOT = ClaudeSlot()
