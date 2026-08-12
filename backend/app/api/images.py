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

from urllib.parse import urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import Response

from app.connectors.http_fetch import USER_AGENT

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BYTES = 15 * 1024 * 1024  # 15 MB — generous for a single image, not unbounded
_TIMEOUT = 8.0


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


async def proxy_image(request: Request) -> Response:
    url = request.query_params.get("url")
    if not url:
        return Response(status_code=400)

    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        return Response(status_code=400)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT) as client:
            headers = {"User-Agent": USER_AGENT, "Referer": referer_for(url)}
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return Response(status_code=502)

    if resp.status_code != 200:
        return Response(status_code=502)

    body = resp.content[:_MAX_BYTES]

    content_type = resp.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        # Origin mislabeled it (a real, observed case: Qiniu-backed
        # s3.ifanr.com serves genuine PNGs as application/octet-stream) —
        # fall back to sniffing the actual bytes before giving up.
        sniffed = sniff_image_type(body)
        if sniffed is None:
            return Response(status_code=415)
        content_type = sniffed
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
