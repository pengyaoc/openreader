"""Read/write helpers over the SQLite schema, shared by the API layer.
Each function takes an open connection — no connection management here.

Every helper here except save_summary takes a `user_id` right after the
connection (multi-user, 2026-08-31): feeds and articles are shared, but
read and starred state is per-account and lives in article_states. Summaries
are the deliberate exception — they're a property of the article, cached
once and read by everyone.

The absence of an article_states row means unread and unstarred, so read
paths go through COALESCE over a LEFT JOIN rather than reading a column,
and write paths are upserts rather than UPDATEs.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

# The per-source unread aggregate, shared verbatim by list_sources and
# get_source so the two read paths can't drift on how a count is computed.
# Takes one bound parameter (user_id).
_UNREAD_SUBQUERY = """
        LEFT JOIN (
            SELECT a.source_id, COUNT(*) AS unread_count
            FROM articles a
            LEFT JOIN article_states st ON st.article_id = a.id AND st.user_id = ?
            WHERE COALESCE(st.is_read, 0) = 0
            GROUP BY a.source_id
        ) u ON u.source_id = s.id
"""


def list_sources(
    conn: sqlite3.Connection, user_id: int, valid_keys: set[str] | None = None
) -> list[dict]:
    """valid_keys, when given, restricts results to sources still present in
    the currently-loaded config — a source's DB row is create-only (written
    once by get_or_create_source the first time it's refreshed) and is
    never deleted just because it's later removed from feeds.yaml, so
    without this filter a removed source keeps showing in the sidebar
    forever. The API layer passes the live config's source keys; callers
    that omit it (tests, internal tooling) get the unfiltered DB list."""
    # A pre-aggregated subquery (rather than a correlated count per source)
    # so the sidebar's whole unread picture is one pass over articles joined
    # to this user's state, not one pass per source.
    query = f"""
        SELECT s.id, s.key, s.type, s.title, s.folder, s.last_fetched_at,
               s.last_error,
               COALESCE(u.unread_count, 0) AS unread_count
        FROM sources s
        {_UNREAD_SUBQUERY}
    """
    # user_id first: the subquery's placeholder is textually ahead of the
    # valid_keys IN(...) list appended below, and bind order follows text.
    params: list = [user_id]
    if valid_keys is not None:
        placeholders = ", ".join("?" for _ in valid_keys)
        query += f" WHERE s.key IN ({placeholders})" if valid_keys else " WHERE 0"
        params.extend(valid_keys)
    query += " ORDER BY s.folder, s.title"

    rows = conn.execute(query, params).fetchall()
    return [_source_row_to_dict(r) for r in rows]


def get_source(conn: sqlite3.Connection, user_id: int, source_id: int) -> dict | None:
    """Single-source counterpart to list_sources, same column/aggregation
    shape (including unread_count) so both read paths agree on the count."""
    row = conn.execute(
        f"""SELECT s.id, s.key, s.type, s.title, s.folder, s.last_fetched_at,
                   s.last_error,
                   COALESCE(u.unread_count, 0) AS unread_count
            FROM sources s
            {_UNREAD_SUBQUERY}
            WHERE s.id = ?""",
        (user_id, source_id),
    ).fetchone()
    return _source_row_to_dict(row) if row else None


def _source_row_to_dict(r) -> dict:
    return {
        "id": r[0],
        "key": r[1],
        "type": r[2],
        "title": r[3],
        "folder": r[4],
        "last_fetched_at": r[5],
        "last_error": r[6],
        "unread_count": r[7],
    }


# articles' own columns. is_read/read_at/is_starred are conspicuously
# absent: they moved to article_states (2026-08-31) and are selected from
# the join below instead, appended to every row in _STATE_COLUMNS order so
# the JSON shape the frontend sees is unchanged.
_ARTICLE_COLUMNS = (
    "id", "source_id", "guid", "url", "canonical_url", "title", "author",
    "published_at", "fetched_at", "excerpt", "content_html", "top_image_path",
    "matched_rule", "origin",
    "hydrated_at", "hydrate_failed_at",
    "llm_summary_html", "llm_summary_at",
)

_STATE_COLUMNS = ("is_read", "read_at", "is_starred")

# No article_states row means unread and unstarred, so every read path goes
# through COALESCE rather than reading the column directly. read_at needs no
# default — NULL is already "never read".
_STATE_SELECT = (
    "COALESCE(st.is_read, 0) AS is_read, "
    "st.read_at AS read_at, "
    "COALESCE(st.is_starred, 0) AS is_starred"
)
_STATE_JOIN = "LEFT JOIN article_states st ON st.article_id = a.id AND st.user_id = ?"

# The list endpoint (list_articles) backs the sidebar's article rows, which
# only ever render title/excerpt/meta fields — never the full article body.
# content_html and llm_summary_html average tens of KB each; shipping them
# on every row of every page made a 50-item page ~570KB of JSON the client
# immediately discarded. Dropped from the list query below; get_article
# (the single-article detail fetch) still selects the full _ARTICLE_COLUMNS.
_LIST_COLUMNS = tuple(c for c in _ARTICLE_COLUMNS if c not in ("content_html", "llm_summary_html"))


def _row_to_article(row) -> dict:
    d = dict(zip(_ARTICLE_COLUMNS + _STATE_COLUMNS, row))
    d["is_read"] = bool(d["is_read"])
    d["is_starred"] = bool(d["is_starred"])
    return d


def list_articles(
    conn: sqlite3.Connection,
    user_id: int,
    view: str = "all",
    source_id: int | None = None,
    folder: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    cols = ", ".join(f"a.{c}" for c in _LIST_COLUMNS)
    if view == "starred":
        # An inner join, unlike every other view: it lets SQLite drive the
        # whole query from idx_article_states_starred over this user's
        # handful of starred rows instead of scanning every article and
        # probing state per row. Same result set either way — a starred
        # article has a state row by definition.
        state_join = (
            "JOIN article_states st ON st.article_id = a.id "
            "AND st.user_id = ? AND st.is_starred = 1"
        )
    else:
        state_join = _STATE_JOIN

    query = f"""
        SELECT {cols}, {_STATE_SELECT},
               (a.llm_summary_html IS NOT NULL) AS has_summary, s.title AS source_title
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        {state_join}
        WHERE 1=1
    """
    # user_id first, for the state join — textually ahead of every filter.
    params: list = [user_id]
    if view == "unread":
        query += " AND COALESCE(st.is_read, 0) = 0"
    if source_id is not None:
        query += " AND a.source_id = ?"
        params.append(source_id)
    if folder is not None:
        query += " AND s.folder = ?"
        params.append(folder)
    query += " ORDER BY a.published_at DESC, a.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = conn.execute(query, params).fetchall()
    result = []
    n = len(_LIST_COLUMNS) + len(_STATE_COLUMNS)
    for row in rows:
        article = dict(zip(_LIST_COLUMNS + _STATE_COLUMNS, row[:n]))
        article["is_read"] = bool(article["is_read"])
        article["is_starred"] = bool(article["is_starred"])
        article["has_summary"] = bool(row[n])
        article["source_title"] = row[n + 1]
        result.append(article)
    return result


def get_article(conn: sqlite3.Connection, user_id: int, article_id: int) -> dict | None:
    # Joined with sources for source_title, matching list_articles — without
    # it, the reader has no source name to fall back on when an article has
    # no author (common: feed-level bylines, Twitter-sourced RSS, etc. often
    # omit one), and the byline area in the UI just goes blank.
    cols = ", ".join(f"a.{c}" for c in _ARTICLE_COLUMNS)
    row = conn.execute(
        f"""SELECT {cols}, {_STATE_SELECT}, s.title AS source_title
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            {_STATE_JOIN}
            WHERE a.id = ?""",
        (user_id, article_id),
    ).fetchone()
    if row is None:
        return None
    article = _row_to_article(row[:-1])
    article["source_title"] = row[-1]
    return article


# Every write below is an upsert rather than an UPDATE, because the row it
# needs to change may not exist yet — an untouched article has no state row
# for this user at all. The `SELECT 1 FROM articles` pre-checks are still
# there for the 404 contract callers rely on, and because foreign_keys=ON
# would otherwise turn an unknown article id into an IntegrityError rather
# than a clean None/False.
def mark_read(conn: sqlite3.Connection, user_id: int, article_id: int) -> bool:
    if conn.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is None:
        return False
    conn.execute(
        """
        INSERT INTO article_states (user_id, article_id, is_read, read_at)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(user_id, article_id) DO UPDATE SET
            is_read = 1, read_at = excluded.read_at
        WHERE article_states.is_read = 0
        """,
        (user_id, article_id, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return True


# The trailing WHERE on each SELECT below is doing two jobs. It scopes the
# sweep to rows this user hasn't read — so rowcount is exactly the number
# flipped and a repeat call returns 0 — and it is also what makes ON
# CONFLICT parseable at all: after `JOIN ... ON`, SQLite can't tell where
# the join condition ends and the upsert clause begins without it. Don't
# "simplify" it away.
_MARK_ALL_READ = """
    INSERT INTO article_states (user_id, article_id, is_read, read_at)
    SELECT ?, a.id, 1, ?
    FROM articles a
    LEFT JOIN article_states st ON st.article_id = a.id AND st.user_id = ?
    WHERE {scope} COALESCE(st.is_read, 0) = 0
    ON CONFLICT(user_id, article_id) DO UPDATE SET
        is_read = 1, read_at = excluded.read_at
"""


def mark_all_read(conn: sqlite3.Connection, user_id: int, source_id: int) -> int | None:
    """Bulk counterpart to mark_read(), scoped to every article this user
    hasn't read on one source. Returns None if the source doesn't exist,
    else the number of rows actually flipped (already-read articles aren't
    touched, so a second call against the same source returns 0 —
    idempotent), and never touches another account's state."""
    row = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        _MARK_ALL_READ.format(scope="a.source_id = ? AND"),
        (user_id, datetime.now(UTC).isoformat(), user_id, source_id),
    )
    conn.commit()
    return cur.rowcount


def mark_all_read_global(conn: sqlite3.Connection, user_id: int) -> int:
    """Bulk counterpart to mark_all_read(), scoped to every article this
    user hasn't read across every source at once — backs the "mark all
    read" action on the Unread saved view itself. Returns the number of
    rows flipped."""
    cur = conn.execute(
        _MARK_ALL_READ.format(scope=""),
        (user_id, datetime.now(UTC).isoformat(), user_id),
    )
    conn.commit()
    return cur.rowcount


def save_summary(conn: sqlite3.Connection, article_id: int, summary_html: str) -> str:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE articles SET llm_summary_html = ?, llm_summary_at = ? WHERE id = ?",
        (summary_html, now, article_id),
    )
    conn.commit()
    return now


def toggle_star(conn: sqlite3.Connection, user_id: int, article_id: int) -> dict | None:
    if conn.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is None:
        return None
    row = conn.execute(
        "SELECT is_starred FROM article_states WHERE user_id = ? AND article_id = ?",
        (user_id, article_id),
    ).fetchone()
    new_val = 0 if (row and row[0]) else 1
    # is_read/read_at are deliberately outside this statement: on insert
    # they take the table's unread defaults, and on conflict they're left
    # alone, so starring never disturbs read state (or vice versa, below).
    conn.execute(
        """
        INSERT INTO article_states (user_id, article_id, is_starred)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, article_id) DO UPDATE SET is_starred = excluded.is_starred
        """,
        (user_id, article_id, new_val),
    )
    conn.commit()
    return {"is_starred": bool(new_val)}


def toggle_read(conn: sqlite3.Connection, user_id: int, article_id: int) -> dict | None:
    """Manual read/unread toggle (the 'm' shortcut). Distinct from
    mark_read(), which is the one-directional, idempotent call fired when
    the fullscreen reader opens.

    Toggling back to unread leaves an all-default state row behind rather
    than deleting it. Harmless — an is_read=0/is_starred=0 row reads
    identically to no row — and pruning would put an extra branch on a
    write path for no measurable gain at this scale.
    """
    if conn.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is None:
        return None
    row = conn.execute(
        "SELECT is_read FROM article_states WHERE user_id = ? AND article_id = ?",
        (user_id, article_id),
    ).fetchone()
    new_val = 0 if (row and row[0]) else 1
    conn.execute(
        """
        INSERT INTO article_states (user_id, article_id, is_read, read_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, article_id) DO UPDATE SET
            is_read = excluded.is_read, read_at = excluded.read_at
        """,
        (user_id, article_id, new_val, datetime.now(UTC).isoformat() if new_val else None),
    )
    conn.commit()
    return {"is_read": bool(new_val)}
