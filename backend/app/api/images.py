"""Image proxy: GET /api/img?url=<encoded>. Exists because many sites
hotlink-protect on Referer — an <img src> pointed straight at the original
URL sends our own origin as Referer and silently fails to load. Fetching
server-side sidesteps that and avoids leaking the reader's IP/referrer to
third-party image hosts on every view.

Two real, contradictory hotlink policies were found in the wild:
  - dapenti.com blocks a *foreign* Referer (a bare request with none, or
    one matching its own host, is fine).
  - sspai.com's CDN requires *a* same-site Referer to be present at all —
    a bare request with none gets a 403.
A fixed "always send no Referer" or "always send our own origin" policy
can't satisfy both. Deriving the Referer from the image URL's own origin
(same-site by construction) satisfies both — verified live against each.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import Response

from app.connectors.http_fetch import USER_AGENT
from app.netsafety import SsrfBlocked
from app.netsafety import assert_safe_url as _assert_safe_url

_MAX_BYTES = 15 * 1024 * 1024  # 15 MB — generous for a single image, not unbounded
_TIMEOUT = 8.0
_MAX_REDIRECTS = 3


_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _shared_client() -> httpx.AsyncClient:
    """One pooled client for the process, instead of a fresh one per image:
    an article's images usually come from one or two CDNs, and a new client
    per request paid a full TCP+TLS handshake to the same host for every
    image. Keyed to the running loop because an AsyncClient's connections
    belong to the loop that opened them (uvicorn has one; the test client
    may start several)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client = httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT)
        _client_loop = loop
    return _client


def referer_for(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(body: bytes) -> str | None:
    """Identifies an image format from its magic bytes, independent of
    whatever Content-Type the origin claims. Needed because some CDNs
    (Qiniu-backed s3.ifanr.com among them, hosting Lark/Feishu-pasted
    images) serve genuine images as `application/octet-stream` — trusting
    Content-Type alone rejects real images on those origins."""
    for magic, media_type in _MAGIC_SIGNATURES:
        if body.startswith(magic):
            return media_type
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


class _FetchFailed(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


async def _fetch_image(client: httpx.AsyncClient, url: str) -> tuple[str, bytes]:
    """Follows redirects by hand and returns (content type, body), raising
    _FetchFailed with the status to answer otherwise.

    follow_redirects=False and a manual hop loop, rather than httpx's
    built-in follow_redirects=True: each redirect target has to pass the
    same public-address check as the original URL, or a private/loopback
    SSRF becomes reachable via a 302 from an otherwise-public host.

    The body is streamed and abandoned past _MAX_BYTES. Reading it whole
    first (resp.content) held an oversized image entirely in memory before
    the cap could apply, on a ~1GB VM."""
    current_url = url
    headers = {"User-Agent": USER_AGENT, "Referer": referer_for(current_url)}
    for _ in range(_MAX_REDIRECTS):
        async with client.stream("GET", current_url, headers=headers) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise _FetchFailed(502)
                current_url = urljoin(current_url, location)
                try:
                    await asyncio.to_thread(_assert_safe_url, current_url)
                except SsrfBlocked:
                    raise _FetchFailed(400) from None
                headers["Referer"] = referer_for(current_url)
                continue
            if resp.status_code != 200:
                raise _FetchFailed(502)
            chunks = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise _FetchFailed(502)
                chunks.append(chunk)
            return resp.headers.get("Content-Type", ""), b"".join(chunks)
    raise _FetchFailed(502)  # too many redirects


def _image_response(content_type: str, body: bytes) -> Response:
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def proxy_image(request: Request) -> Response:
    url = request.query_params.get("url")
    if not url:
        return Response(status_code=400)

    # A cached image already passed the SSRF check (and the type check
    # below) when it was first fetched, so a hit skips straight to it — no
    # DNS lookup, no origin round trip.
    cache = request.app.state.image_cache
    cached = await asyncio.to_thread(cache.get, url)
    if cached is not None:
        return _image_response(*cached)

    try:
        await asyncio.to_thread(_assert_safe_url, url)
    except SsrfBlocked:
        return Response(status_code=400)

    try:
        content_type, body = await _fetch_image(_shared_client(), url)
    except _FetchFailed as exc:
        return Response(status_code=exc.status_code)
    except httpx.HTTPError:
        return Response(status_code=502)

    if not content_type.startswith("image/"):
        # Origin mislabeled it (a real, observed case: Qiniu-backed
        # s3.ifanr.com serves genuine PNGs as application/octet-stream) —
        # fall back to sniffing the actual bytes before giving up.
        sniffed = sniff_image_type(body)
        if sniffed is None:
            return Response(status_code=415)
        content_type = sniffed
    await asyncio.to_thread(cache.put, url, content_type, body)
    return _image_response(content_type, body)
