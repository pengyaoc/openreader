"""SQLite storage. WAL mode, stdlib sqlite3 only — no ORM.

Schema per the design doc:
  sources   — one row per configured RSS/IMAP source
  articles  — normalized items from any origin (feed | email)
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    folder TEXT NOT NULL,
    url TEXT,
    config_json TEXT,
    etag TEXT,
    last_modified TEXT,
    last_fetched_at TEXT,
    last_error TEXT,
    last_error_at TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    guid TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT,
    title TEXT NOT NULL,
    author TEXT,
    published_at TEXT,
    fetched_at TEXT,
    excerpt TEXT,
    content_html TEXT,
    top_image_path TEXT,
    content_hash TEXT,
    matched_rule TEXT,
    origin TEXT NOT NULL DEFAULT 'feed',
    hydrated_at TEXT,
    hydrate_failed_at TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    read_at TEXT,
    is_starred INTEGER NOT NULL DEFAULT 0,
    llm_summary_html TEXT,
    llm_summary_at TEXT,
    UNIQUE(source_id, guid)
);

CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_unread ON articles(is_read, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source_pub ON articles(source_id, published_at DESC);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    _add_column_if_missing(conn, "articles", "llm_summary_html", "TEXT")
    _add_column_if_missing(conn, "articles", "llm_summary_at", "TEXT")
    # Topic-generation removal, 2026-08-14 (see docs/WORKLOG.md) — confirmed
    # zero rows in `jobs` and zero origin='llm' articles in production
    # before dropping, so this is a clean removal, not a lossy one. Both
    # idempotent: DROP TABLE IF EXISTS is naturally so, and the ADD COLUMN
    # helper's sibling below just catches "already gone" instead of
    # "already there".
    conn.execute("DROP TABLE IF EXISTS jobs")
    conn.commit()
    _drop_column_if_present(conn, "articles", "job_id")
    _drop_column_if_present(conn, "articles", "citations_json")


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    """No migration framework here (see module docstring) — an already
    running deployment's DB predates a schema change, since CREATE TABLE IF
    NOT EXISTS is a no-op against an existing table. ALTER TABLE ADD COLUMN
    picks it up idempotently on next startup."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _drop_column_if_present(conn: sqlite3.Connection, table: str, column: str) -> None:
    """Mirror of _add_column_if_missing for the removal direction —
    ALTER TABLE ... DROP COLUMN needs SQLite 3.35+ (bundled with Python
    3.13's stdlib sqlite3 here, confirmed 3.53.1 both locally and on the
    deploy target)."""
    try:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "no such column" not in str(exc):
            raise


async def run_off_thread(db_path: str | Path, fn: Callable[..., T], *args, **kwargs) -> T:
    """Runs `fn(conn, *args)` on a worker thread via asyncio.to_thread,
    against its own fresh connection — sqlite3 connections default to
    check_same_thread=True, so the request's connection (opened on the
    event loop thread) can't cross into a worker thread. A fresh connection
    here is cheap and matches the app's existing "fresh connection per use"
    pattern (see app.state.get_conn).

    Needed anywhere a handler calls into synchronous, potentially slow I/O
    (a blocking HTTP fetch, an IMAP session) — called directly from an
    async handler, that blocks uvicorn's single event loop for its full
    duration, not just for the one request but for every other request the
    process is handling until it returns."""

    def run() -> T:
        conn = connect(db_path)
        try:
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()

    return await asyncio.to_thread(run)
