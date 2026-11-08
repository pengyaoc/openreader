"""Article chat: multi-turn questions about one article, answered by the
`claude` CLI on the owner's subscription. Design:
docs/superpowers/specs/2026-11-07-article-chat-design.md.

The CLI keeps the conversation. The first turn starts a session with
--session-id <uuid> and sends the article plus the question; later turns
--resume <uuid> and send only the question. SQLite (store.chat_*) keeps a
display copy and is the source of truth: when the CLI's session file is
gone, or a turn failed after the CLI may already have recorded it, the
next turn starts a fresh session seeded from that copy ("reseed").

Unlike Summarize, chat must NOT pass --no-session-persistence. The
subprocess always runs in one fixed cwd, because the CLI files sessions
under ~/.claude/projects/<encoded cwd>/ and --resume only looks there.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable

import nh3

from app import store
from app.claude_cli import (
    BASE_FLAGS,
    ClaudeError,
    Runner,
    SessionMissingError,
    default_runner,
    run_structured,
)
from app.ingest.textutil import plain_text_excerpt

CHAT_SYSTEM_PROMPT = """You are answering questions about one article for a personal RSS reader.
The article text is given in the first message. Answer from the article
first. When a question needs information the article doesn't contain, you
may answer from general knowledge, but put that part in a separate
paragraph starting with <p class="beyond"><b>Beyond the article:</b> so
the reader can see it isn't from the text. If general knowledge contradicts
the article, say so explicitly. Never invent quotes or figures and attribute
them to the article. Keep answers short and conversational — a few
sentences or a short list — unless the question asks for depth. Output HTML
using only: p, br, b, strong, i, em, ul, ol, li, blockquote. Output must
match the provided JSON schema."""

CHAT_JSON_SCHEMA = {
    "type": "object",
    "properties": {"answer_html": {"type": "string"}},
    "required": ["answer_html"],
}

_CHAT_TAGS = {"p", "br", "b", "strong", "i", "em", "ul", "ol", "li", "blockquote"}


def build_chat_command(session_id: str, resume: bool, model: str = "sonnet") -> list[str]:
    return [
        "claude",
        "-p",
        "--resume" if resume else "--session-id",
        session_id,
        "--model",
        model,
        "--system-prompt",
        CHAT_SYSTEM_PROMPT,
        *BASE_FLAGS,
        "--json-schema",
        json.dumps(CHAT_JSON_SCHEMA),
    ]


def _article_block(article_text: str) -> str:
    return f"Article text ({len(article_text.split())} words):\n\n{article_text}"


def build_first_prompt(article_text: str, question: str) -> str:
    return f"{_article_block(article_text)}\n\nQuestion: {question}"


def build_reseed_prompt(article_text: str, history: list[dict], question: str) -> str:
    lines = []
    for message in history:
        if message["role"] == "user":
            lines.append(f"User: {message['body']}")
        else:
            lines.append(f"Assistant: {plain_text_excerpt(message['body'], limit=None)}")
    transcript = "\n".join(lines)
    return f"{_article_block(article_text)}\n\nEarlier conversation:\n{transcript}\n\nNew question: {question}"


def sanitize_chat_html(html: str) -> str:
    """Narrower than textutil.sanitize_html: no links or images, and the one
    class the frontend styles (`beyond` on <p>) survives."""
    return nh3.clean(html or "", tags=_CHAT_TAGS, attributes={}, allowed_classes={"p": {"beyond"}})


def chat_turn(
    conn: sqlite3.Connection,
    user_id: int,
    article_id: int,
    content_html: str,
    question: str,
    cwd: str,
    timeout: float,
    runner: Runner = default_runner,
    new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> list[dict]:
    """Asks one question and returns the two saved messages (user,
    assistant). Raises ClaudeError on failure; nothing is saved then."""
    session = store.get_chat_session(conn, user_id, article_id)
    history = store.get_chat_messages(conn, user_id, article_id)
    article_text = plain_text_excerpt(content_html, limit=None)

    def ask(session_id: str, resume: bool, stdin_text: str) -> str:
        cmd = build_chat_command(session_id, resume=resume)
        return run_structured(cmd, stdin_text, timeout, cwd, runner, key="answer_html")

    def start_new(prompt: str) -> tuple[str, str]:
        # A fresh UUID every time: a failed first turn may have left a
        # session file behind, and reusing its id would be refused.
        session_id = new_id()
        return session_id, ask(session_id, False, prompt)

    if session and not session["needs_reseed"]:
        try:
            session_id = session["session_id"]
            answer = ask(session_id, True, question)
        except SessionMissingError:
            session_id, answer = start_new(build_reseed_prompt(article_text, history, question))
        except ClaudeError:
            store.mark_chat_needs_reseed(conn, user_id, article_id)
            raise
    elif history:
        session_id, answer = start_new(build_reseed_prompt(article_text, history, question))
    else:
        session_id, answer = start_new(build_first_prompt(article_text, question))

    try:
        return store.save_chat_turn(
            conn, user_id, article_id, session_id, question, sanitize_chat_html(answer)
        )
    except sqlite3.Error as exc:
        # The CLI has this turn but SQLite doesn't: never resume a session
        # holding a turn the user can't see. (A first turn has no row to
        # mark; its next attempt mints a new session anyway.)
        conn.rollback()
        store.mark_chat_needs_reseed(conn, user_id, article_id)
        raise ClaudeError(f"could not save the answer: {exc}") from exc
