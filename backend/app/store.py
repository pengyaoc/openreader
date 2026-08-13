"""Read/write helpers over the SQLite schema, shared by the API layer.
Each function takes an open connection — no connection management here.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def list_sources(conn: sqlite3.Connection, valid_keys: set[str] | None = None) -> list[dict]:
    """valid_keys, when given, restricts results to sources still present in
    the currently-loaded config — a source's DB row is create-only (written
    once by get_or_create_source the first time it's refreshed) and is
    never deleted just because it's later removed from feeds.yaml, so
    without this filter a removed source keeps showing in the sidebar
    forever. The API layer passes the live config's source keys; callers
    that omit it (tests, internal tooling) get the unfiltered DB list."""
    query = """
        SELECT s.id, s.key, s.type, s.title, s.folder, s.last_fetched_at,
               s.last_error,
               COUNT(a.id) FILTER (WHERE a.is_read = 0) AS unread_count
        FROM sources s
        LEFT JOIN articles a ON a.source_id = s.id
    """
    params: list = []
    if valid_keys is not None:
        placeholders = ", ".join("?" for _ in valid_keys)
        query += f" WHERE s.key IN ({placeholders})" if valid_keys else " WHERE 0"
        params.extend(valid_keys)
    query += " GROUP BY s.id ORDER BY s.folder, s.title"

    rows = conn.execute(query, params).fetchall()
    return [_source_row_to_dict(r) for r in rows]


def get_source(conn: sqlite3.Connection, source_id: int) -> dict | None:
    """Single-source counterpart to list_sources, same column/aggregation
    shape (including unread_count) so both read paths agree on the count."""
    row = conn.execute(
        """SELECT s.id, s.key, s.type, s.title, s.folder, s.last_fetched_at,
                  s.last_error,
                  COUNT(a.id) FILTER (WHERE a.is_read = 0) AS unread_count
           FROM sources s
           LEFT JOIN articles a ON a.source_id = s.id
           WHERE s.id = ?
           GROUP BY s.id""",
        (source_id,),
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


_ARTICLE_COLUMNS = (
    "id", "source_id", "guid", "url", "canonical_url", "title", "author",
    "published_at", "fetched_at", "excerpt", "content_html", "top_image_path",
    "matched_rule", "origin", "job_id", "citations_json",
    "hydrated_at", "hydrate_failed_at", "is_read", "read_at", "is_starred",
)


def _row_to_article(row) -> dict:
    d = dict(zip(_ARTICLE_COLUMNS, row))
    d["is_read"] = bool(d["is_read"])
    d["is_starred"] = bool(d["is_starred"])
    return d


def list_articles(
    conn: sqlite3.Connection,
    view: str = "all",
    source_id: int | None = None,
    folder: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    cols = ", ".join(f"a.{c}" for c in _ARTICLE_COLUMNS)
    query = f"""
        SELECT {cols}, s.title AS source_title
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE 1=1
    """
    params: list = []
    if view == "unread":
        query += " AND a.is_read = 0"
    elif view == "starred":
        query += " AND a.is_starred = 1"
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
    for row in rows:
        article = _row_to_article(row[: len(_ARTICLE_COLUMNS)])
        article["source_title"] = row[-1]
        result.append(article)
    return result


def get_article(conn: sqlite3.Connection, article_id: int) -> dict | None:
    # Joined with sources for source_title, matching list_articles — without
    # it, the reader has no source name to fall back on when an article has
    # no author (common: feed-level bylines, Twitter-sourced RSS, etc. often
    # omit one), and the byline area in the UI just goes blank.
    cols = ", ".join(f"a.{c}" for c in _ARTICLE_COLUMNS)
    row = conn.execute(
        f"""SELECT {cols}, s.title AS source_title
            FROM articles a JOIN sources s ON s.id = a.source_id
            WHERE a.id = ?""",
        (article_id,),
    ).fetchone()
    if row is None:
        return None
    article = _row_to_article(row[: len(_ARTICLE_COLUMNS)])
    article["source_title"] = row[-1]
    return article


def mark_read(conn: sqlite3.Connection, article_id: int) -> bool:
    row = conn.execute("SELECT is_read FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE articles SET is_read = 1, read_at = ? WHERE id = ? AND is_read = 0",
        (datetime.now(UTC).isoformat(), article_id),
    )
    conn.commit()
    return True


def mark_all_read(conn: sqlite3.Connection, source_id: int) -> int | None:
    """Bulk counterpart to mark_read(), scoped to every unread article on
    one source. Returns None if the source doesn't exist, else the number
    of rows actually flipped (already-read articles aren't touched, so a
    second call against the same source returns 0 — idempotent)."""
    row = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        "UPDATE articles SET is_read = 1, read_at = ? WHERE source_id = ? AND is_read = 0",
        (datetime.now(UTC).isoformat(), source_id),
    )
    conn.commit()
    return cur.rowcount


def mark_all_read_global(conn: sqlite3.Connection) -> int:
    """Bulk counterpart to mark_all_read(), scoped to every unread article
    across every source at once — backs the "mark all read" action on the
    Unread saved view itself. Returns the number of rows flipped."""
    cur = conn.execute(
        "UPDATE articles SET is_read = 1, read_at = ? WHERE is_read = 0",
        (datetime.now(UTC).isoformat(),),
    )
    conn.commit()
    return cur.rowcount


def toggle_star(conn: sqlite3.Connection, article_id: int) -> dict | None:
    row = conn.execute("SELECT is_starred FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        return None
    new_val = 0 if row[0] else 1
    conn.execute("UPDATE articles SET is_starred = ? WHERE id = ?", (new_val, article_id))
    conn.commit()
    return {"is_starred": bool(new_val)}


def toggle_read(conn: sqlite3.Connection, article_id: int) -> dict | None:
    """Manual read/unread toggle (the 'm' shortcut). Distinct from
    mark_read(), which is the one-directional, idempotent call fired when
    the fullscreen reader opens."""
    row = conn.execute("SELECT is_read FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        return None
    new_val = 0 if row[0] else 1
    conn.execute(
        "UPDATE articles SET is_read = ?, read_at = ? WHERE id = ?",
        (new_val, datetime.now(UTC).isoformat() if new_val else None, article_id),
    )
    conn.commit()
    return {"is_read": bool(new_val)}
