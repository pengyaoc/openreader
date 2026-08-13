"""Synchronous refresh loop: the only ingest path in v1. No scheduler, no
background polling. Returns a per-source report so filter rules are easy
to tune interactively (design doc Part 2).

RSS sources are fetched concurrently (see _refresh_rss_batch) — found live,
2026-08-13: 9 sequential RSS fetches took ~7.7s with no single dominant
outlier, just ordinary per-host TLS+network latency summed one at a time.
IMAP stays sequential over one shared connection — see
_refresh_imap_sequential for why.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import Callable

import httpx

from app.config import Config, Source, compile_rule
from app.connectors import imap as imap_connector
from app.connectors.base import NormalizedEntry
from app.connectors.http_fetch import FetchResult, conditional_get
from app.connectors.rss import FeedParseError, parse_feed
from app.ingest.dedup import canonicalize_url, content_hash
from app.ingest.rules import RawArticle, evaluate_rules
from app.ingest.textutil import plain_text_excerpt, proxy_image_urls, tighten_newsletter_whitespace

Fetcher = Callable[[Source, str | None, str | None], FetchResult]
ImapSearchFn = Callable[..., list[str]]
ImapFetchFn = Callable[[object, str], bytes]
ImapConnectFn = Callable[[], object]

# List-view subtitle length. Long enough to actually convey whether an
# article is worth opening, not just echo the first clause of a sentence.
EXCERPT_LIMIT = 900

# Bounded pool for the RSS fetch phase. Enough to overlap most sources'
# network latency in one batch (measured: 9 sources, none over ~2.6s) for a
# single-user app hitting a handful of distinct hosts, without opening an
# unreasonable number of simultaneous connections out of one process.
_RSS_FETCH_CONCURRENCY = 6


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
    """Returns True if a new row was inserted, False if it already existed.

    Dedups on guid first, then content_hash (canonicalize_url(entry.url) +
    normalized title). guid alone isn't as stable as a feed's own <id>/
    <guid> is supposed to be — simonwillison.net's Atom feed started
    appending a #atom-everything fragment to entry ids that previously had
    none, partway through this app's lifetime, minting a "new" guid for
    every already-ingested post on the next refresh and duplicating ~30
    articles (found 2026-08-13 from a live duplicate-articles report).
    content_hash is built from a URL that's already had its fragment (and
    tracking params) stripped by canonicalize_url(), so it survives
    exactly this kind of superficial id drift where guid doesn't. The
    content_hash column and its index already existed for this — this was
    the missing other half, wiring the lookup up to actually use it.
    """
    chash = content_hash(entry.url, entry.title) if entry.url else None
    existing = conn.execute(
        """SELECT id FROM articles
           WHERE source_id = ?
             AND (guid = ? OR (content_hash IS NOT NULL AND content_hash = ?))""",
        (source_id, entry.guid, chash),
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
    if origin == "email":
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
            chash,
            matched_rule_str,
            origin,
        ),
    )
    return True


def refresh_source(conn: sqlite3.Connection, source: Source, fetcher: Fetcher) -> dict:
    """Single-source fetch + persist, both on the calling thread. The
    RSS batch path (_refresh_rss_batch) instead fetches many sources
    concurrently first, then calls _persist_rss_result (this function's
    second half, factored out below) sequentially — this function stays
    as the direct single-source entry point tests and callers use."""
    source_id, etag, last_modified = get_or_create_source(conn, source)
    result, exc = _rss_fetch_only(fetcher, source, etag, last_modified)
    return _persist_rss_result(conn, source, source_id, result, exc)


def _rss_fetch_only(
    fetcher: Fetcher, source: Source, etag: str | None, last_modified: str | None
) -> tuple[FetchResult | None, Exception | None]:
    """The network-bound half of a source refresh — no DB access, safe to
    run on a worker thread. Never raises: catches and returns the
    exception instead, so a thread-pool map can collect every source's
    result (success or failure) without one bad source aborting the batch."""
    try:
        return fetcher(source, etag, last_modified), None
    except Exception as exc:  # noqa: BLE001 — isolates this source
        return None, exc


def _persist_rss_result(
    conn: sqlite3.Connection,
    source: Source,
    source_id: int,
    result: FetchResult | None,
    exc: Exception | None,
) -> dict:
    """The DB-writing half — always runs on the connection's own thread."""
    now = datetime.now(UTC).isoformat()

    if exc is not None:
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
    except FeedParseError as parse_exc:
        conn.execute(
            "UPDATE sources SET last_error = ?, last_error_at = ? WHERE id = ?",
            (str(parse_exc), now, source_id),
        )
        conn.commit()
        return {"key": source.key, "status": "error", "error": str(parse_exc)}

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


def _refresh_rss_batch(conn: sqlite3.Connection, sources: list[Source], fetcher: Fetcher) -> dict[str, dict]:
    """Fetches every given RSS source concurrently, then persists results
    sequentially on the calling thread. Returns a dict keyed by source key
    so the caller can reassemble reports in the original source order.

    get_or_create_source() runs upfront, sequentially — it's a cheap local
    DB read/insert, not worth parallelizing, and it has to happen before
    the fetch anyway (need each source's etag/last_modified to send). The
    fetches themselves are the actual cost (network + TLS per distinct
    host) and the only part that's safe to run off-thread: sqlite3
    connections default to check_same_thread=True, so every DB write below
    has to stay on the thread that opened `conn`, not a pool worker.
    """
    prepared = [(s, *get_or_create_source(conn, s)) for s in sources]

    with concurrent.futures.ThreadPoolExecutor(max_workers=_RSS_FETCH_CONCURRENCY) as pool:
        fetch_results = list(
            pool.map(
                lambda p: _rss_fetch_only(fetcher, p[0], p[2], p[3]),
                prepared,
            )
        )

    reports = {}
    for (source, source_id, _etag, _last_modified), (result, exc) in zip(prepared, fetch_results):
        reports[source.key] = _persist_rss_result(conn, source, source_id, result, exc)
    return reports


_IMAP_OVERLAP_DAYS = 1  # IMAP SEARCH SINCE is date-only (no time-of-day), so
# the overlap has to be a whole day, not a few minutes — otherwise a message
# that arrives after this run's SINCE boundary but before the exact same
# calendar day next runs could be missed at the edge. Cheap: _persist_entry
# already dedupes on (source_id, guid), so re-seeing a day's worth of
# messages is a no-op, not a duplicate.
_IMAP_DEFAULT_WINDOW_DAYS = 30  # first-refresh bound when the source's query
# has no newer_than:Nd token, to avoid backfilling years of inbox history
# on the very first run.


def _scoped_imap_since(last_fetched_at: str | None, default_window_days: int) -> datetime:
    if last_fetched_at:
        return datetime.fromisoformat(last_fetched_at) - timedelta(days=_IMAP_OVERLAP_DAYS)
    return datetime.now(UTC) - timedelta(days=default_window_days)


def refresh_imap_source(
    conn: sqlite3.Connection,
    source: Source,
    imap_client,
    search_fn: ImapSearchFn = imap_connector.search_message_ids,
    fetch_fn: ImapFetchFn = imap_connector.fetch_message,
) -> dict:
    """SEARCH for message ids scoped to messages since the last successful
    refresh, then FETCH+persist only the ones not already ingested.
    Read-only: opens the mailbox with readonly=True and only ever calls
    SEARCH/FETCH, never STORE/EXPUNGE.
    """
    source_id, _, _ = get_or_create_source(conn, source)
    last_fetched_at = conn.execute(
        "SELECT last_fetched_at FROM sources WHERE id = ?", (source_id,)
    ).fetchone()[0]
    now = datetime.now(UTC).isoformat()

    from_filter, subject_filter, newer_than_days = imap_connector.parse_query(source.query)
    since = _scoped_imap_since(last_fetched_at, newer_than_days or _IMAP_DEFAULT_WINDOW_DAYS)

    try:
        message_ids = search_fn(
            imap_client,
            source.mailbox_folder or imap_connector.DEFAULT_FOLDER,
            since=since,
            from_filter=from_filter,
            subject_filter=subject_filter,
        )
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
        # IMAP sequence/UID numbers aren't a stable guid across sessions —
        # the dedup check below is on the parsed entry's Message-Id, after
        # fetch, not on message_id itself.
        try:
            raw = fetch_fn(imap_client, message_id)
        except Exception:  # noqa: BLE001 — one bad message must not sink the refresh
            continue

        entry = imap_connector.parse_message(raw)
        already = conn.execute(
            "SELECT id FROM articles WHERE source_id = ? AND guid = ?", (source_id, entry.guid)
        ).fetchone()
        if already:
            continue

        passed, matched = evaluate_rules(_entry_to_raw_article(entry), rules)
        if not passed:
            filtered_count += 1
            continue
        if _persist_entry(conn, source_id, entry, matched, origin="email"):
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


def _mark_source_error(conn: sqlite3.Connection, source: Source, exc: Exception) -> dict:
    """Records a source-level failure and returns its report dict — shared
    by callers that fail before ever reaching a per-source fetch (e.g. one
    connect_fn covering several sources), so the error is stamped on every
    affected source without re-deriving the same UPDATE at each call site."""
    source_id, _, _ = get_or_create_source(conn, source)
    conn.execute(
        "UPDATE sources SET last_error = ?, last_error_at = ? WHERE id = ?",
        (str(exc), datetime.now(UTC).isoformat(), source_id),
    )
    conn.commit()
    return {"key": source.key, "status": "error", "error": str(exc)}


def _refresh_imap_sequential(
    conn: sqlite3.Connection,
    sources: list[Source],
    connect_fn: ImapConnectFn,
    search_fn: ImapSearchFn = imap_connector.search_message_ids,
    fetch_fn: ImapFetchFn = imap_connector.fetch_message,
) -> dict[str, dict]:
    """One shared IMAP connection, reused sequentially across every source
    in this refresh (not one connection each, like RSS) — a concurrent
    per-source connection made Gmail silently stall connections from this
    app's datacenter IP; see docs/WORKLOG.md 2026-08-13. Logs out once at
    the end. Assumes a handful of imap sources, matching this app's
    single-user scope — a much larger source count would need pooling
    rather than one login serving all of them.
    """
    try:
        client = connect_fn()
    except Exception as exc:  # noqa: BLE001 — one failed connect fails every imap source this refresh
        return {s.key: _mark_source_error(conn, s, exc) for s in sources}

    try:
        return {
            s.key: refresh_imap_source(conn, s, client, search_fn, fetch_fn)
            for s in sources
        }
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001 — best-effort cleanup only
            pass


def refresh_all(
    conn: sqlite3.Connection,
    sources: list[Source],
    fetcher: Fetcher | None = None,
    only_key: str | None = None,
    imap_connect: ImapConnectFn | None = None,
) -> dict:
    if fetcher is None:
        with httpx.Client() as client:
            return refresh_all(
                conn,
                sources,
                fetcher=_default_fetcher(client),
                only_key=only_key,
                imap_connect=imap_connect,
            )

    started = time.monotonic()
    to_refresh = [s for s in sources if only_key is None or s.key == only_key]

    # RSS sources are fetched as one concurrent batch, IMAP sequentially
    # over one shared connection — see _refresh_rss_batch/_refresh_imap_sequential.
    # Results are keyed by source key so the final report list matches
    # `to_refresh`'s original order regardless of how source types are
    # interleaved in config.
    rss_sources = [s for s in to_refresh if s.type == "rss"]
    rss_reports = _refresh_rss_batch(conn, rss_sources, fetcher) if rss_sources else {}

    imap_sources = [s for s in to_refresh if s.type == "imap"]
    if not imap_sources:
        imap_reports: dict[str, dict] = {}
    elif imap_connect is None:
        imap_reports = {
            s.key: {
                "key": s.key,
                "status": "error",
                "error": "IMAP not configured — set READER_IMAP_HOST/_USER/_PASSWORD",
            }
            for s in imap_sources
        }
    else:
        imap_reports = _refresh_imap_sequential(conn, imap_sources, imap_connect)

    reports_by_key: dict[str, dict] = {**rss_reports, **imap_reports}
    reports = [reports_by_key[s.key] for s in to_refresh]

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {"elapsed_ms": elapsed_ms, "sources": reports}


def reconcile_read_state(conn: sqlite3.Connection, config: Config) -> int:
    """Runs when config is saved (PUT /api/config): any already-fetched,
    still-unread article whose source no longer exists in the new config,
    or whose source's current rules it no longer passes, gets marked
    read — same effect as the user reading it, via the same mark_read-
    style guarded UPDATE. Nothing is deleted or hidden; these articles
    stay fully visible in All items/Starred, same as any other read
    article (feedback 2026-08-13: "any feed items that don't meet the
    criteria should be marked as read... they can still stay in the DB").

    Only inspects is_read=0 rows — already-read articles need no action
    whether a person read them or this same function did on a previous
    config save, and read_at for a genuinely-user-read article is never
    touched. Returns the number of articles marked.
    """
    valid_keys = {s.key for s in config.sources}
    rules_by_key = {s.key: [compile_rule(r) for r in s.rules] for s in config.sources}
    now = datetime.now(UTC).isoformat()
    marked = 0

    for source_id, key in conn.execute("SELECT id, key FROM sources").fetchall():
        if key not in valid_keys:
            cur = conn.execute(
                "UPDATE articles SET is_read = 1, read_at = ? WHERE source_id = ? AND is_read = 0",
                (now, source_id),
            )
            marked += cur.rowcount
            continue

        rules = rules_by_key[key]
        if not rules:
            continue  # no rules on this source -> nothing can fail to pass

        rows = conn.execute(
            """SELECT id, title, excerpt, content_html, author, url
               FROM articles WHERE source_id = ? AND is_read = 0""",
            (source_id,),
        ).fetchall()
        for article_id, title, excerpt, content_html, author, url in rows:
            raw = RawArticle(
                title=title or "", summary=excerpt or "", content=content_html or "",
                author=author or "", url=url or "",
            )
            passed, _ = evaluate_rules(raw, rules)
            if not passed:
                conn.execute(
                    "UPDATE articles SET is_read = 1, read_at = ? WHERE id = ?",
                    (now, article_id),
                )
                marked += 1

    conn.commit()
    return marked
