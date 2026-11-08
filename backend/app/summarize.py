"""Wraps the `claude` CLI for on-demand, single-article summarization —
one of the reader's two LLM features, next to article chat (topic-generation was removed 2026-08-14,
see docs/WORKLOG.md; this module absorbed the parts of it worth keeping).

The subscription-auth rules (never --bare, scrub ANTHROPIC_API_KEY), the
shared footprint flags, envelope parsing and the one-at-a-time slot live in
app/claude_cli.py, shared with article chat (app/chat.py). Summarize adds
--no-session-persistence: it is a one-shot call with nothing to resume.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable

from app.claude_cli import BASE_FLAGS, ClaudeError, default_runner, run_structured
from app.ingest.textutil import sanitize_html

__all__ = ["ClaudeError", "build_summarize_command", "build_user_prompt", "summarize_text"]

# Summarize's runner takes (cmd, stdin, timeout) — no cwd; it never resumes.
Runner = Callable[[list[str], str, float], subprocess.CompletedProcess]


SUMMARIZE_SYSTEM_PROMPT = """You are summarizing a single article for a personal RSS reader.

You will be given the full text of one article. Write a summary based
strictly on that text.

Rules:
- Use only information explicitly stated in the article text below. Never
  add outside knowledge, speculation, or invented facts, figures, or
  quotes. If the article doesn't say it, the summary doesn't say it.
- Write in the author's own voice, as a condensed version of the article
  itself — not as a narrator describing it. State the article's claims and
  points directly ("Rates will stay high through 2027 because..."), never
  as reported speech ("The author argues that...", "This piece by X
  describes...", "The article explains..."). Keep the author's person: if
  the article is written in first person, the summary uses "I"/"we" too.
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
        *BASE_FLAGS,
        "--no-session-persistence",
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
    return default_runner(cmd, stdin_text, timeout, None)


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
    summary_html = run_structured(
        cmd, prompt, timeout, None, lambda c, s, t, _cwd: runner(c, s, t), key="summary_html"
    )
    return sanitize_html(summary_html)
