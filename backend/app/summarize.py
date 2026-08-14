"""Wraps the `claude` CLI for on-demand, single-article summarization —
the reader's only LLM feature (topic-generation was removed 2026-08-14,
see docs/WORKLOG.md; this module absorbed the parts of it worth keeping).

Runs against the Claude subscription (OAuth keychain), not the API key —
two rules make that true and must never be relaxed:
  1. Never pass --bare (its own help text: under --bare, "Anthropic auth is
     strictly ANTHROPIC_API_KEY or apiKeyHelper ... OAuth and keychain are
     never read" — that would silently bill the API).
  2. Scrub ANTHROPIC_API_KEY from the child environment.

--strict-mcp-config and --setting-sources "" matter for footprint and
determinism: without them every call would boot whatever MCP servers,
CLAUDE.md files, and hooks happen to be configured locally. --tools ""
disables all tools — nothing to research, the article text is handed to
the model directly.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from app.ingest.textutil import sanitize_html


class ClaudeError(Exception):
    """Raised for any failure in the summarization call — non-zero exit,
    timeout, malformed output, or a reported model-side error."""


Runner = Callable[[list[str], str, float], subprocess.CompletedProcess]

SUMMARIZE_SYSTEM_PROMPT = """You are summarizing a single article for a personal RSS reader.

You will be given the full text of one article. Write a summary based
strictly on that text.

Rules:
- Use only information explicitly stated in the article text below. Never
  add outside knowledge, speculation, or invented facts, figures, or
  quotes. If the article doesn't say it, the summary doesn't say it.
- Optimize for readability: use bullet points (<ul>/<li>) to break up
  distinct points, and <b> or <strong> to highlight key terms, numbers, or
  names where it helps scanning.
- Output HTML using only these tags: p, br, b, strong, i, em, ul, ol, li,
  blockquote. No images, no scripts, no styling, no links.
- Output must match the provided JSON schema exactly."""

SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "properties": {"summary_html": {"type": "string"}},
    "required": ["summary_html"],
}


def build_summarize_command(model: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--system-prompt",
        SUMMARIZE_SYSTEM_PROMPT,
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SUMMARY_JSON_SCHEMA),
    ]


def build_user_prompt(article_text: str, word_count: int) -> str:
    target_min = max(100, round(word_count * 0.2))
    return (
        f"Article text ({word_count} words):\n\n{article_text}\n\n"
        f"Write a summary of at least {target_min} words — proportional to "
        f"the length of the article above, not a fixed short blurb."
    )


def _default_runner(cmd: list[str], stdin_text: str, timeout: float) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    return subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout, env=env
    )


def summarize_text(
    article_text: str,
    word_count: int,
    model: str = "sonnet",
    timeout: float = 300.0,
    runner: Runner = _default_runner,
) -> str:
    """Runs claude with the summarization system prompt and returns sanitized
    summary_html. Raises ClaudeError on any failure — non-zero exit, timeout,
    malformed output, or a reported model-side error."""
    cmd = build_summarize_command(model)
    prompt = build_user_prompt(article_text, word_count)

    try:
        result = runner(cmd, prompt, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"summarization timed out after {timeout}s") from exc

    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: {result.stderr[:500]}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"could not parse claude output as JSON: {exc}") from exc

    if envelope.get("is_error"):
        raise ClaudeError(f"claude reported an error: {envelope.get('result')}")

    structured = envelope.get("structured_output")
    if not isinstance(structured, dict) or "summary_html" not in structured:
        raise ClaudeError("claude output missing structured_output.summary_html")

    return sanitize_html(structured["summary_html"])
