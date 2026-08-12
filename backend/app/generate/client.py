"""Wraps the `claude` CLI for out-of-process article generation.

Runs against the Claude subscription (OAuth keychain), not the API key —
two rules make that true and must never be relaxed:
  1. Never pass --bare (its own help text: under --bare, "Anthropic auth is
     strictly ANTHROPIC_API_KEY or apiKeyHelper ... OAuth and keychain are
     never read" — that would silently bill the API).
  2. Scrub ANTHROPIC_API_KEY from the child environment.

--strict-mcp-config and --setting-sources "" matter for footprint and
determinism: without them every call would boot whatever MCP servers,
CLAUDE.md files, and hooks happen to be configured locally.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from app.generate.prompt import ARTICLES_JSON_SCHEMA, GENERATOR_SYSTEM_PROMPT


class ClaudeError(Exception):
    """Raised for any failure in the generation call — non-zero exit,
    timeout, malformed output, or a reported model-side error. Callers
    should treat this as 'write zero articles', never partial results."""


Runner = Callable[[list[str], str, float], subprocess.CompletedProcess]


def build_command(model: str, json_schema: dict) -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--system-prompt",
        GENERATOR_SYSTEM_PROMPT,
        "--tools",
        "WebSearch,WebFetch",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        # bypassPermissions, not dontAsk: verified live that dontAsk silently
        # *denies* every WebSearch/WebFetch call in headless mode (denials
        # show up in the response's permission_denials array, and the run
        # completes anyway with zero real research done — a silent failure
        # mode, not an error). --tools already restricts the tool set to
        # WebSearch/WebFetch, so bypassing the interactive prompt for just
        # those two read-only tools is what actually makes generation work.
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(json_schema),
    ]


def _default_runner(cmd: list[str], stdin_text: str, timeout: float) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    return subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout, env=env
    )


def generate_articles(
    prompt: str,
    model: str = "sonnet",
    timeout: float = 600.0,
    runner: Runner = _default_runner,
) -> list[dict]:
    """Runs claude with the article-generation system prompt and returns the
    parsed `articles` list. Raises ClaudeError on any failure — the caller
    persists nothing in that case, matching the "zero articles beats a
    half-generated feed" rule."""
    cmd = build_command(model, ARTICLES_JSON_SCHEMA)

    try:
        result = runner(cmd, prompt, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"generation timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: {result.stderr[:500]}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"could not parse claude output as JSON: {exc}") from exc

    if envelope.get("is_error"):
        raise ClaudeError(f"claude reported an error: {envelope.get('result')}")

    structured = envelope.get("structured_output")
    if not isinstance(structured, dict) or "articles" not in structured:
        raise ClaudeError("claude output missing structured_output.articles")

    return structured["articles"]
