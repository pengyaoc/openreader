"""Synchronous refresh loop: the only ingest path in v1. No scheduler, no
background polling. Fetches sources sequentially and returns a per-source
report so filter rules are easy to tune interactively (design doc Part 2).
"""
from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from typing import Callable

import httpx

from app.config import Source, compile_rule
from app.connectors.base import NormalizedEntry
from app.connectors.gmail import get_message, list_message_ids, parse_message
from app.connectors.http_fetch import FetchResult, conditional_get
from app.connectors.rss import FeedParseError, parse_feed
from app.ingest.dedup import canonicalize_url, content_hash
from app.ingest.rules import RawArticle, evaluate_rules
from app.ingest.textutil import plain_text_excerpt, proxy_image_urls, tighten_newsletter_whitespace

Fetcher = Callable[[Source, str | None, str | None], FetchResult]
GmailListFn = Callable[[str, str], list[str]]
GmailGetFn = Callable[[str, str], dict]

# List-view subtitle length. Long enough to actually convey whether an
# article is worth opening, not just echo the first clause of a sentence.
EXCERPT_LIMIT = 900


def _default_fetcher(client: httpx.Client) -> Fetcher:
    def fetch(source: Source, etag: str | None, last_modified: str | None) -> FetchResult:
        return conditional_get(client, source.url, etag, last_modified)

    return fetch


def get_or_create_source(conn: sqlite3.Connection, source: Source) -> tuple[int, str | None, str | None]:
    row = conn.execute(
        "SELECT id, etag, last_modified FROM sources WHERE key = ?", (source.key,)
    ).fetchone()
    if row:
        return row[0], row[1], row[2]

    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES (?, ?, ?, ?, ?)",
        (source.key, source.type, source.title, source.folder, source.url),
    )
    conn.commit()
    source_id = conn.execute("SELECT id FROM sources WHERE key = ?", (source.key,)).fetchone()[0]
    return source_id, None, None


def _entry_to_raw_article(entry: NormalizedEntry) -> RawArticle:
    return RawArticle(
        title=entry.title,
        summary=entry.summary,
        content=entry.content_html,
        author=entry.author,
        url=entry.url,
    )


def _persist_entry(
    conn: sqlite3.Connection,
    source_id: int,
    entry: NormalizedEntry,
    matched_rule,
    origin: str = "feed",
) -> bool:
    """Returns True if a new row was inserted, False if it already existed."""
    existing = conn.execute(
        "SELECT id FROM articles WHERE source_id = ? AND guid = ?", (source_id, entry.guid)
    ).fetchone()
    if existing:
        return False

    # Prefer content:encoded, but many feeds (dapenti.com/xilei among them)
    # put the full HTML body — images included — straight into <description>
    # with no content:encoded at all. Falling back keeps that content instead
    # of silently dropping it and showing only a truncated excerpt forever.
    raw_content = entry.content_html or entry.summary
    excerpt = plain_text_excerpt(raw_content, limit=EXCERPT_LIMIT)
    content_html = proxy_image_urls(raw_content)
    if origin == "gmail":
        content_html = tighten_newsletter_whitespace(content_html)
    matched_rule_str = (
        f"{matched_rule.action}/{matched_rule.field}/{matched_rule.pattern}" if matched_rule else None
    )
    conn.execute(
        """INSERT INTO articles
           (source_id, guid, url, canonical_url, title, author, published_at,
            fetched_at, excerpt, content_html, content_hash, matched_rule, origin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            entry.guid,
            entry.url,
            canonicalize_url(entry.url) if entry.url else None,
            entry.title,
            entry.author,
            entry.published_at,
            datetime.now(UTC).isoformat(),
            excerpt,
            content_html,
            content_hash(entry.url, entry.title) if entry.url else None,
            matched_rule_str,
            origin,
        ),
    )
    return True


def refresh_source(conn: sqlite3.Connection, source: Source, fetcher: Fetcher) -> dict:
    source_id, etag, last_modified = get_or_create_source(conn, source)
    now = datetime.now(UTC).isoformat()

    try:
        result = fetcher(source, etag, last_modified)
    except Exception as exc:  # noqa: BLE001 — any fetch failure isolates this source
        conn.execute(
            "UPDATE sources SET last_error = ?, last_error_at = ? WHERE id = ?",
            (str(exc), now, source_id),
        )
        conn.commit()
        return {"key": source.key, "status": "error", "error": str(exc)}

    if result.status == 304:
        conn.execute(
            "UPDATE sources SET last_fetched_at = ?, last_error = NULL WHERE id = ?",
            (now, source_id),
        )
        conn.commit()
        return {"key": source.key, "status": "not_modified"}

    try:
        entries = parse_feed(result.body)
    except FeedParseError as exc:
        conn.execute(
            "UPDATE sources SET last_error = ?, last_error_at = ? WHERE id = ?",
            (str(exc), now, source_id),
        )
        conn.commit()
        return {"key": source.key, "status": "error", "error": str(exc)}

    rules = [compile_rule(r) for r in source.rules]
    new_count = 0
    filtered_count = 0
    for entry in entries:
        passed, matched = evaluate_rules(_entry_to_raw_article(entry), rules)
        if not passed:
            filtered_count += 1
            continue
        if _persist_entry(conn, source_id, entry, matched):
            new_count += 1

    conn.execute(
        """UPDATE sources
           SET etag = ?, last_modified = ?, last_fetched_at = ?, last_error = NULL
           WHERE id = ?""",
        (result.etag, result.last_modified, now, source_id),
    )
    conn.commit()

    return {
        "key": source.key,
        "status": "ok",
        "fetched": len(entries),
        "new": new_count,
        "filtered": filtered_count,
    }


_GMAIL_OVERLAP_SECONDS = 300  # re-check a small trailing window on every
# refresh so a message Gmail's search index hadn't caught up on yet during
# the previous run isn't permanently missed. Cheap: overlap just means a
# handful of message ids get re-listed, and _persist_entry already dedupes
# on (source_id, guid), so re-seeing one is a no-op, not a duplicate.


def _scoped_gmail_query(query: str, last_fetched_at: str | None) -> str:
    """Narrows a source's Gmail query to messages since the last successful
    refresh, so a routine refresh lists only what's new instead of
    re-walking the source's entire configured window (e.g. `newer_than:30d`)
    every single time."""
    if not last_fetched_at:
        return query
    epoch = int(datetime.fromisoformat(last_fetched_at).timestamp()) - _GMAIL_OVERLAP_SECONDS
    return f"{query} after:{epoch}"


def refresh_gmail_source(
    conn: sqlite3.Connection,
    source: Source,
    access_token: str,
    list_fn: GmailListFn = list_message_ids,
    get_fn: GmailGetFn = get_message,
) -> dict:
    """Mirrors refresh_source's shape but for Gmail: list message ids for
    the source's query — scoped to messages since the last successful
    refresh — fetch+persist only the ones not already ingested. Read-only —
    only ever calls list/get, never touches labels or content.
    """
    source_id, _, _ = get_or_create_source(conn, source)
    last_fetched_at = conn.execute(
        "SELECT last_fetched_at FROM sources WHERE id = ?", (source_id,)
    ).fetchone()[0]
    now = datetime.now(UTC).isoformat()

    scoped_query = _scoped_gmail_query(source.query, last_fetched_at)
    try:
        message_ids = list_fn(access_token, scoped_query)
    except Exception as exc:  # noqa: BLE001 — isolates this source, matches refresh_source
        conn.execute(
            "UPDATE sources SET last_error = ?, last_error_at = ? WHERE id = ?",
            (str(exc), now, source_id),
        )
        conn.commit()
        return {"key": source.key, "status": "error", "error": str(exc)}

    rules = [compile_rule(r) for r in source.rules]
    new_count = 0
    filtered_count = 0
    for message_id in message_ids:
        already = conn.execute(
            "SELECT id FROM articles WHERE source_id = ? AND guid = ?", (source_id, message_id)
        ).fetchone()
        if already:
            continue

        try:
            raw = get_fn(access_token, message_id)
        except Exception:  # noqa: BLE001 — one bad message must not sink the refresh
            continue

        entry = parse_message(raw)
        passed, matched = evaluate_rules(_entry_to_raw_article(entry), rules)
        if not passed:
            filtered_count += 1
            continue
        if _persist_entry(conn, source_id, entry, matched, origin="gmail"):
            new_count += 1

    conn.execute(
        "UPDATE sources SET last_fetched_at = ?, last_error = NULL WHERE id = ?",
        (now, source_id),
    )
    conn.commit()

    return {
        "key": source.key,
        "status": "ok",
        "fetched": len(message_ids),
        "new": new_count,
        "filtered": filtered_count,
    }


def refresh_all(
    conn: sqlite3.Connection,
    sources: list[Source],
    fetcher: Fetcher | None = None,
    only_key: str | None = None,
    gmail_access_token: str | None = None,
) -> dict:
    if fetcher is None:
        with httpx.Client() as client:
            return refresh_all(
                conn,
                sources,
                fetcher=_default_fetcher(client),
                only_key=only_key,
                gmail_access_token=gmail_access_token,
            )

    started = time.monotonic()
    to_refresh = [s for s in sources if only_key is None or s.key == only_key]

    reports = []
    for s in to_refresh:
        if s.type == "rss":
            reports.append(refresh_source(conn, s, fetcher))
        elif s.type == "gmail":
            if gmail_access_token is None:
                reports.append(
                    {
                        "key": s.key,
                        "status": "error",
                        "error": "Gmail not authenticated — run scripts/gmail_auth.py",
                    }
                )
            else:
                reports.append(refresh_gmail_source(conn, s, gmail_access_token))

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {"elapsed_ms": elapsed_ms, "sources": reports}
