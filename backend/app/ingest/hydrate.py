"""Full-text hydration (design doc Part 2).

Two entry points:
  hydrate_article  — synchronous, one article, backs GET /api/articles/:id's
                      passive on-open hydration and POST .../hydrate's
                      explicit pull. Fetches at most once ever — a stored
                      hydrated_at or hydrate_failed_at short-circuits every
                      call after the first, whether it succeeded or not.
  hydrate_pending   — batch, run after a refresh so most articles are
                      already hydrated by the time a user opens them,
                      instead of paying a live ~5s fetch inline on GET.
                      POST /api/refresh starts it via hydrate_in_background
                      so the response doesn't wait for it.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import httpx

from app.connectors.http_fetch import USER_AGENT
from app.db import connect
from app.ingest.extract import extract_readable
from app.ingest.textutil import EXCERPT_LIMIT, plain_text_excerpt, proxy_image_urls

_SUBSTANTIAL_LEN = 600
Fetcher = Callable[[str, float], str]

# Same reasoning as refresh.py's _RSS_FETCH_CONCURRENCY: bounded overlap of
# per-host network latency, not an unbounded fan-out. Raised from 6 to 10 on
# 2026-10-17: the benchmark's first refresh spent 4.7s of its 5.2s here, in
# ~17 waves of 6. Full-text sources are mostly link aggregators whose
# articles are spread across many hosts, so this rarely means 10 requests
# to one site.
_HYDRATE_CONCURRENCY = 10

# Cap per refresh so a first refresh against a large backlog (e.g. importing
# an OPML with years of unread articles) doesn't turn one refresh into an
# unbounded batch of HTTP fetches. Anything left over gets picked up by the
# next refresh, or lazily on open same as before this change.
_HYDRATE_BATCH_LIMIT = 100


_shared_client: httpx.Client | None = None
_shared_client_lock = threading.Lock()


def _default_fetcher(url: str, timeout: float) -> str:
    """The on-open fetch's fetcher. One pooled client for the process
    (httpx.Client is thread-safe), so opening several articles from the
    same site reuses a connection instead of a new TCP+TLS handshake
    each time."""
    global _shared_client
    with _shared_client_lock:
        if _shared_client is None:
            _shared_client = httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        client = _shared_client
    resp = client.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _client_fetcher(client: httpx.Client) -> Fetcher:
    """Batch counterpart to _default_fetcher: shares one connection pool
    across the batch, so articles on the same host reuse a connection
    instead of each paying its own TCP+TLS handshake."""

    def fetch(url: str, timeout: float) -> str:
        resp = client.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text

    return fetch


def fetch_and_extract(url: str, timeout: float, fetcher: Fetcher | None = None) -> str:
    """Pure network + parsing step, no DB — the part of hydration that's
    safe to run concurrently across a thread pool. Returns "" (never
    raises) on any failure, mirroring hydrate_article's existing
    any-exception-falls-back-to-excerpt behavior."""
    fetcher = fetcher or _default_fetcher
    try:
        page_html = fetcher(url, timeout)
        return proxy_image_urls(extract_readable(page_html, base_url=url))
    except Exception:  # noqa: BLE001 — any failure falls back to the excerpt
        return ""


def hydrate_article(
    conn: sqlite3.Connection,
    article_id: int,
    fetch_full_text: bool,
    fetcher: Fetcher | None = None,
    timeout: float = 5.0,
) -> dict:
    """Returns {"content_html": str} — either the existing/hydrated content,
    or "" to signal the caller should fall back to the stored excerpt."""
    row = conn.execute(
        "SELECT url, content_html, hydrated_at, hydrate_failed_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return {"content_html": ""}

    url, content_html, hydrated_at, hydrate_failed_at = row

    if hydrated_at is not None or hydrate_failed_at is not None:
        return {"content_html": content_html or ""}

    if content_html and len(content_html) >= _SUBSTANTIAL_LEN:
        return {"content_html": content_html}

    if not fetch_full_text or not url:
        return {"content_html": content_html or ""}

    now = datetime.now(UTC).isoformat()
    extracted = fetch_and_extract(url, timeout, fetcher)

    if not extracted:
        conn.execute(
            "UPDATE articles SET hydrate_failed_at = ? WHERE id = ?", (now, article_id)
        )
        conn.commit()
        return {"content_html": ""}

    conn.execute(
        "UPDATE articles SET content_html = ?, excerpt = ?, hydrated_at = ? WHERE id = ?",
        (extracted, plain_text_excerpt(extracted, limit=EXCERPT_LIMIT), now, article_id),
    )
    conn.commit()
    return {"content_html": extracted}


def hydrate_pending(
    conn: sqlite3.Connection,
    source_fetch_full_text: dict[str, bool],
    limit: int = _HYDRATE_BATCH_LIMIT,
    timeout: float = 5.0,
    fetcher: Fetcher | None = None,
) -> int:
    """Batch counterpart to hydrate_article, called at the end of a refresh
    so article bodies are usually already in SQLite by the time a user
    opens them — GET /api/articles/:id's hydrate_article call then just
    short-circuits on hydrated_at (an indexed row read) instead of doing a
    live fetch inline on the read path.

    source_fetch_full_text maps source key -> whether that source wants
    full-text hydration (same fetch_full_text resolution GET .../:id uses:
    per-source config falling back to config.defaults.fetch_full_text).
    Sources not in the map, or mapped to False, are skipped entirely.

    Returns the number of articles successfully hydrated (failures and
    skips aren't counted).
    """
    eligible_keys = [k for k, v in source_fetch_full_text.items() if v]
    if not eligible_keys:
        return 0

    if fetcher is None:
        client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=_HYDRATE_CONCURRENCY),
        )
        with client:
            return hydrate_pending(
                conn, source_fetch_full_text, limit=limit, timeout=timeout, fetcher=_client_fetcher(client)
            )

    placeholders = ", ".join("?" for _ in eligible_keys)
    rows = conn.execute(
        f"""
        SELECT a.id, a.url
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE a.origin = 'feed'
          AND a.hydrated_at IS NULL
          AND a.hydrate_failed_at IS NULL
          AND (a.content_html IS NULL OR length(a.content_html) < ?)
          AND s.key IN ({placeholders})
        ORDER BY a.published_at DESC
        LIMIT ?
        """,
        [_SUBSTANTIAL_LEN, *eligible_keys, limit],
    ).fetchall()
    if not rows:
        return 0

    # Network+parse fan-out happens off the calling connection entirely —
    # fetch_and_extract is pure, and every write happens here on the
    # calling thread, avoiding any cross-thread sqlite3 connection use.
    # Each result is written as soon as its fetch finishes rather than after
    # the whole batch (2026-10-27): a batch is up to 100 fetches with 5s
    # timeouts, and until an article's row was written, opening it fell
    # back to a second, inline fetch that the reader had to wait for.
    hydrated = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=_HYDRATE_CONCURRENCY) as pool:
        futures = {
            pool.submit(fetch_and_extract, url, timeout, fetcher): article_id
            for article_id, url in rows
            if url
        }
        for fut in concurrent.futures.as_completed(futures):
            article_id = futures[fut]
            extracted = fut.result()
            now = datetime.now(UTC).isoformat()
            # The hydrated_at/hydrate_failed_at IS NULL guard: an on-open
            # fetch may have settled this article while its batch fetch was
            # in flight, and the first result written stays.
            if extracted:
                cur = conn.execute(
                    """UPDATE articles SET content_html = ?, excerpt = ?, hydrated_at = ?
                       WHERE id = ? AND hydrated_at IS NULL AND hydrate_failed_at IS NULL""",
                    (extracted, plain_text_excerpt(extracted, limit=EXCERPT_LIMIT), now, article_id),
                )
                hydrated += cur.rowcount
            else:
                conn.execute(
                    """UPDATE articles SET hydrate_failed_at = ?
                       WHERE id = ? AND hydrated_at IS NULL AND hydrate_failed_at IS NULL""",
                    (now, article_id),
                )
            conn.commit()
    return hydrated


# Single-flight state for hydrate_in_background. A refresh that arrives
# while a previous refresh's hydration is still running doesn't start a
# second batch, which could fetch the same articles twice. It queues its
# scope, and the running batch does one more pass when it's done.
_background_lock = threading.Lock()
_background_running = False
_background_queued: dict[str, bool] | None = None


def hydrate_in_background(db_path: str | Path, source_fetch_full_text: dict[str, bool]) -> None:
    """Runs hydrate_pending on a daemon thread with its own connection and
    returns at once. Used by POST /api/refresh (2026-10-17) so the response,
    and the new articles in the list, don't wait on up to 100 page fetches.
    Each article is still fetched at most once. One opened before its batch
    reaches it falls back to the lazy on-open fetch in GET
    /api/articles/:id, as before batch hydration existed.

    Still triggered only by a refresh someone asked for, never by a timer.
    If the process stops mid-batch nothing is lost: hydrate_pending writes
    each article as it finishes, and the next refresh picks up the ones it
    hadn't reached."""
    global _background_running, _background_queued
    if not any(source_fetch_full_text.values()):
        return
    with _background_lock:
        if _background_running:
            queued = _background_queued or {}
            _background_queued = {
                k: queued.get(k, False) or source_fetch_full_text.get(k, False)
                for k in queued.keys() | source_fetch_full_text.keys()
            }
            return
        _background_running = True

    threading.Thread(
        target=_run_background_hydration,
        args=(db_path, source_fetch_full_text),
        name="hydrate",
        daemon=True,
    ).start()


def _run_background_hydration(db_path: str | Path, scope: dict[str, bool]) -> None:
    global _background_running, _background_queued
    while True:
        try:
            conn = connect(db_path)
            try:
                hydrate_pending(conn, scope)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — a background batch must not die silently
            traceback.print_exc()
        with _background_lock:
            if _background_queued is None:
                _background_running = False
                return
            scope, _background_queued = _background_queued, None
