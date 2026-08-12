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

import ipaddress
import socket
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import Response

from app.connectors.http_fetch import USER_AGENT

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BYTES = 15 * 1024 * 1024  # 15 MB — generous for a single image, not unbounded
_TIMEOUT = 8.0
_MAX_REDIRECTS = 3


def referer_for(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


class SsrfBlocked(Exception):
    """Raised when a URL — or a redirect target — resolves to a non-public
    address. Without this, /api/img is an open forwarder: it takes any URL
    from an unauthenticated request and fetches it server-side, which is
    exactly the shape of a request needed to reach the VM's own localhost
    services or GCP's internal network."""


def _is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _assert_public_host(host: str) -> None:
    """Resolves `host` and rejects it if *any* resolved address is not
    publicly routable — checking every address, not just the first, since a
    host can round-robin between a public and an internal one.

    This check and the connection httpx eventually makes are not atomic: a
    DNS record could change between this resolve and httpx's own connect
    ("DNS rebinding"). Closing that gap needs a custom transport that pins
    the resolved IP into the TCP connection, which is out of scope here.
    What this closes is the straightforward case actually seen against open
    image proxies — a URL that directly names a private, loopback, or
    metadata address (e.g. 127.0.0.1, 169.254.169.254, 10.0.0.0/8).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfBlocked(f"could not resolve host: {host}") from exc
    for _family, _type, _proto, _canon, sockaddr in infos:
        if not _is_public_address(sockaddr[0]):
            raise SsrfBlocked(f"host {host!r} resolves to a non-public address")


def _assert_safe_url(url: str) -> SplitResult:
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.hostname:
        raise SsrfBlocked(f"unsupported or invalid URL: {url}")
    _assert_public_host(parts.hostname)
    return parts


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

    try:
        _assert_safe_url(url)
    except SsrfBlocked:
        return Response(status_code=400)

    # follow_redirects=False and a manual hop loop, rather than httpx's
    # built-in follow_redirects=True: each redirect target has to pass the
    # same public-address check as the original URL, or a private/loopback
    # SSRF becomes reachable via a 302 from an otherwise-public host.
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT) as client:
            current_url = url
            headers = {"User-Agent": USER_AGENT, "Referer": referer_for(current_url)}
            for _ in range(_MAX_REDIRECTS):
                resp = await client.get(current_url, headers=headers)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                location = resp.headers.get("Location")
                if not location:
                    return Response(status_code=502)
                current_url = urljoin(current_url, location)
                try:
                    _assert_safe_url(current_url)
                except SsrfBlocked:
                    return Response(status_code=400)
                headers["Referer"] = referer_for(current_url)
            else:
                return Response(status_code=502)  # too many redirects
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
