"""Web feed builder: POST /api/webfeed/preview {url, selector?}.

Fetches a page the user pasted, and returns what a type=web source built
from it would ingest — the auto-detected candidate link groups (plus the
user's own selector, when given), the page's advertised RSS/Atom feeds,
and its title. Subscribing is then just POST /api/sources with
`type: web` and the chosen selector; nothing here writes.

Signed-in only: this makes the server fetch an arbitrary URL, so anonymous
visitors of the public demo must not be able to drive it. Every hop is
also SSRF-checked (app/netsafety.py), same as the image proxy.
"""
from __future__ import annotations

import asyncio

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api._common import AnonymousUserError, require_user_id
from app.connectors.rss import FeedParseError, parse_feed
from app.connectors.webpage import (
    extract_candidates,
    extract_with_selector,
    find_feed_links,
    page_title,
)
from app.netsafety import FetchTooLarge, SsrfBlocked, safe_get

_PREVIEW_ITEMS = 10  # items shown per candidate — the full count is reported separately


def _normalize_url(raw: str) -> str:
    raw = raw.strip()
    if "://" not in raw:
        raw = "https://" + raw
    return raw


def _is_feed(content_type: str, body: bytes) -> bool:
    if not any(t in content_type for t in ("xml", "rss", "atom")):
        return False
    try:
        parse_feed(body)
    except FeedParseError:
        return False
    return True


def _candidate_json(selector: str, entries, custom: bool = False) -> dict:
    return {
        "selector": selector,
        "custom": custom,
        "count": len(entries),
        "items": [
            {
                "title": e.title,
                "url": e.url,
                "published_at": e.published_at,
                "summary": e.summary,
            }
            for e in entries[:_PREVIEW_ITEMS]
        ],
    }


def build_preview(url: str, selector: str | None) -> dict:
    """Blocking: fetch + extract. Raises PreviewError with a user-facing
    message for every expected failure."""
    try:
        fetched = safe_get(url)
    except SsrfBlocked:
        raise PreviewError("That address can't be fetched — only public websites are allowed.")
    except FetchTooLarge as exc:
        raise PreviewError(f"Couldn't load the page: {exc}.")
    except httpx.HTTPError as exc:
        raise PreviewError(f"Couldn't load the page: {exc.__class__.__name__}: {exc}")

    if fetched.status != 200:
        raise PreviewError(f"The site responded with HTTP {fetched.status}.")

    if _is_feed(fetched.content_type.lower(), fetched.body):
        return {
            "final_url": fetched.url,
            "page_title": "",
            "feed_links": [fetched.url],
            "candidates": [],
        }

    content_type = fetched.content_type.lower()
    if content_type and "html" not in content_type:
        raise PreviewError(f"That URL isn't a web page (it's {content_type.split(';')[0]}).")

    candidates = []
    if selector:
        candidates.append(
            _candidate_json(selector, extract_with_selector(fetched.body, fetched.url, selector), custom=True)
        )
    for candidate in extract_candidates(fetched.body, fetched.url):
        if candidate.selector != selector:
            candidates.append(_candidate_json(candidate.selector, candidate.entries))

    return {
        "final_url": fetched.url,
        "page_title": page_title(fetched.body),
        "feed_links": find_feed_links(fetched.body, fetched.url),
        "candidates": candidates,
    }


class PreviewError(Exception):
    pass


async def preview(request: Request) -> JSONResponse:
    try:
        require_user_id(request)
    except AnonymousUserError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    try:
        body = await request.json()
    except ValueError:
        body = {}
    raw_url = (body.get("url") or "").strip() if isinstance(body, dict) else ""
    if not raw_url:
        return JSONResponse({"error": "url is required"}, status_code=400)
    selector = (body.get("selector") or "").strip() or None

    try:
        result = await asyncio.to_thread(build_preview, _normalize_url(raw_url), selector)
    except PreviewError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result)
