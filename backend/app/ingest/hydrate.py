"""Lazy, per-article full-text hydration (design doc Part 2). Triggered by
GET /api/articles/:id, never by refresh. Fetches the article's own page at
most once, ever — a stored hydrated_at or hydrate_failed_at short-circuits
every call after the first, whether it succeeded or not.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Callable

import httpx

from app.connectors.http_fetch import USER_AGENT
from app.ingest.extract import extract_readable
from app.ingest.textutil import proxy_image_urls

_SUBSTANTIAL_LEN = 600
Fetcher = Callable[[str, float], str]


def _default_fetcher(url: str, timeout: float) -> str:
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def hydrate_article(
    conn: sqlite3.Connection,
    article_id: int,
    fetch_full_text: bool,
    fetcher: Fetcher | None = None,
    timeout: float = 5.0,
) -> dict:
    """Returns {"content_html": str} — either the existing/hydrated content,
    or "" to signal the caller should fall back to the stored excerpt."""
    fetcher = fetcher or _default_fetcher

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
    try:
        page_html = fetcher(url, timeout)
        extracted = proxy_image_urls(extract_readable(page_html, base_url=url))
    except Exception:  # noqa: BLE001 — any failure falls back to the excerpt
        conn.execute(
            "UPDATE articles SET hydrate_failed_at = ? WHERE id = ?", (now, article_id)
        )
        conn.commit()
        return {"content_html": ""}

    if not extracted:
        conn.execute(
            "UPDATE articles SET hydrate_failed_at = ? WHERE id = ?", (now, article_id)
        )
        conn.commit()
        return {"content_html": ""}

    conn.execute(
        "UPDATE articles SET content_html = ?, hydrated_at = ? WHERE id = ?",
        (extracted, now, article_id),
    )
    conn.commit()
    return {"content_html": extracted}
