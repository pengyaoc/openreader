"""SQLite storage. WAL mode, stdlib sqlite3 only — no ORM.

Schema per the design doc:
  sources         — one row per configured RSS/IMAP source
  articles        — normalized items from any origin (feed | email)
  users           — one row per account (2026-08-31, see docs/WORKLOG.md)
  article_states  — per-user read/starred state for an article

Feeds are shared: `sources` and `articles` are global, and so is
config/feeds.yaml. Only what a *person* does to an article — reading it,
starring it — is per-user, and that lives in article_states.
"""
from __future__ import annotations

import asyncio
import html
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

# The account that owns pre-multi-user read/starred state (the backfill in
# _migrate_per_user_state targets it), and the identity every request is
# attributed to when login is switched off entirely — see app/auth.py's
# AuthMiddleware and api/_common.current_user_id. Insertion order in the
# users table is id order, so this is simply "the first account created".
DEFAULT_USER_ID = 1

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
    llm_summary_html TEXT,
    llm_summary_at TEXT,
    UNIQUE(source_id, guid)
);

CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_source_pub ON articles(source_id, published_at DESC);
-- Trailing `, id DESC` matches list_articles' ORDER BY tiebreaker (ties on
-- published_at aren't rare — many feeds/newsletters share a timestamp) so
-- SQLite can satisfy the sort straight from the index instead of falling
-- back to a temp b-tree for the tiebreak. Since read/starred moved out of
-- this table (2026-08-31) this one index backs the ordering for *every*
-- view, not just "all" — the read/starred predicate now lives in a joined
-- table and can no longer be a prefix of the ordering index.
CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(published_at DESC, id DESC);
-- Backs the folder filter in list_articles.
CREATE INDEX IF NOT EXISTS idx_sources_folder ON sources(folder);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    password_changed_at TEXT
);

-- Per-user read/starred state. A *missing row means unread and unstarred*,
-- which is the load-bearing property of this table: it's what lets a newly
-- added account start with every existing article unread without writing a
-- row per (user, article) pair, and what keeps this table proportional to
-- what people actually touch rather than to users x articles.
CREATE TABLE IF NOT EXISTS article_states (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    is_read INTEGER NOT NULL DEFAULT 0,
    read_at TEXT,
    is_starred INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, article_id)
) WITHOUT ROWID;

-- WITHOUT ROWID above: the primary key is the whole addressing story for
-- this table, so the implicit rowid plus a separate PK index would be pure
-- overhead on every read and write.

-- Backs the starred view, which inner-joins article_states and drives off
-- this index over one user's handful of starred rows (see
-- store.list_articles) rather than scanning every article.
CREATE INDEX IF NOT EXISTS idx_article_states_starred
    ON article_states(user_id, is_starred);
-- Reverse direction: reconcile_read_state's per-article fan-out across
-- users, and the article_id foreign key's ON DELETE CASCADE.
CREATE INDEX IF NOT EXISTS idx_article_states_article
    ON article_states(article_id);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # busy_timeout: retry internally instead of raising "database is locked"
    # when a background hydrate/refresh thread holds the write lock briefly.
    # synchronous=NORMAL: safe under WAL (only risks losing the last commit
    # on an OS crash, not corruption) and meaningfully cheaper than FULL.
    # temp_store/cache_size: keep the ORDER BY temp b-trees that do still
    # happen (e.g. folder-filtered queries) in memory rather than on disk,
    # with a larger page cache for this small, frequently-read DB.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-16000")
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
    # "already there". articles.job_id must be dropped first — with
    # foreign_keys=ON (see connect()), SQLite refuses to drop a table that a
    # surviving column still references.
    _drop_column_if_present(conn, "articles", "job_id")
    _drop_column_if_present(conn, "articles", "citations_json")
    conn.execute("DROP TABLE IF EXISTS jobs")
    conn.commit()
    # Multi-user, 2026-08-31 (see docs/WORKLOG.md). Order is load-bearing:
    # article_states rows reference users(id), so an owner must exist before
    # anything can be backfilled onto it.
    ensure_default_user(conn)
    _migrate_per_user_state(conn)
    _backfill_decoded_entities(conn)


def ensure_default_user(conn: sqlite3.Connection) -> None:
    """Fresh-DB safety net only: guarantees DEFAULT_USER_ID exists so the
    per-user state model always has an owner, including on a brand-new DB
    and in the test suite (which reaches every endpoint with auth off, and
    would otherwise trip article_states' foreign key). Does nothing at all
    once any account exists.

    Deliberately *not* a place to seed the real accounts from config: an
    env-var-driven bootstrap that ran on first startup after a deploy would
    silently attribute an entire production read history to whatever name
    happened to be configured — or defaulted to — at that moment, which is
    only recoverable by hand-editing the DB. Real accounts are created
    explicitly with `python -m app.useradm add`, where getting the name
    wrong is visible immediately.
    """
    from app import settings

    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        ("reader", settings.AUTH_PASSWORD_HASH or "", datetime.now(UTC).isoformat()),
    )
    conn.commit()


_LEGACY_STATE_COLUMNS = ("is_read", "read_at", "is_starred")
# Indexes that referenced those columns and therefore have to go with them.
_LEGACY_STATE_INDEXES = (
    "idx_articles_unread",
    "idx_articles_starred",
    "idx_articles_src_unread",
)


def _migrate_per_user_state(conn: sqlite3.Connection, owner_user_id: int = DEFAULT_USER_ID) -> None:
    """Moves articles.is_read/read_at/is_starred — the single-user era's
    state — into article_states rows owned by `owner_user_id`, then drops
    the columns. "Not yet migrated" is detected by the columns still
    existing, the same shape as the ADD/DROP COLUMN helpers below: no
    migration framework, no schema-version table.

    Only rows that are actually read or starred get a state row. Everything
    else stays absent, which *is* the unread default — so every other
    account starts with the full existing backlog unread, which is the
    intent (a second reader hasn't read any of it).

    Safe to interrupt: if the process dies between the backfill and the
    drops, the columns are still present on the next startup, the backfill
    re-runs, and every already-migrated row hits ON CONFLICT DO NOTHING.
    Nothing is double-counted or lost.
    """
    columns = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
    if not set(_LEGACY_STATE_COLUMNS) & columns:
        return

    conn.execute(
        """
        INSERT INTO article_states (user_id, article_id, is_read, read_at, is_starred)
        SELECT ?, id, is_read, read_at, is_starred
        FROM articles
        WHERE is_read = 1 OR is_starred = 1
        ON CONFLICT(user_id, article_id) DO NOTHING
        """,
        (owner_user_id,),
    )
    conn.commit()

    # DROP INDEX before DROP COLUMN, always: SQLite refuses to drop a column
    # any index still references, and it raises "error in index ... after
    # drop column" — which is *not* the "no such column" that
    # _drop_column_if_present tolerates, so it would crash startup instead.
    for index in _LEGACY_STATE_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    for column in _LEGACY_STATE_COLUMNS:
        _drop_column_if_present(conn, "articles", column)
    conn.commit()


def _backfill_decoded_entities(conn: sqlite3.Connection) -> None:
    """Fixes articles ingested before connectors/rss.py started decoding
    double-encoded entities (`&amp;#8217;` where a feed meant `&#8217;`,
    which the XML parser only half-resolves — see that file's `_text()`
    docstring, 2026-08-31). The parser fix only applies going forward: an
    article's title/author is written once at ingest and never revisited
    on a later refetch (dedup is by guid), so anything already in the DB
    with the old, half-decoded parser kept the raw `&#8217;` forever.

    No "have we run this" flag needed — comparing each row against its own
    unescaped form is the idempotence check. Once every row is decoded,
    nothing differs and this is one full-table SELECT with zero writes.
    Cheap at this data size (hundreds of rows); safe to run on every
    startup, same spirit as the ADD/DROP COLUMN helpers' repeatability.
    """
    rows = conn.execute("SELECT id, title, author FROM articles").fetchall()
    updates = []
    for article_id, title, author in rows:
        new_title = html.unescape(title) if title else title
        new_author = html.unescape(author) if author else author
        if new_title != title or new_author != author:
            updates.append((new_title, new_author, article_id))
    if not updates:
        return
    conn.executemany(
        "UPDATE articles SET title = ?, author = ? WHERE id = ?", updates
    )
    conn.commit()


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
