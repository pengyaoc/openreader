"""SSRF guard shared by every endpoint that fetches a URL a request
supplied: the image proxy (/api/img) and the web feed builder's preview
(/api/webfeed/preview). Moved here from app/api/images.py 2026-09-28 when
the second caller arrived.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import NamedTuple
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx

from app.connectors.http_fetch import USER_AGENT

_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class SsrfBlocked(Exception):
    """Raised when a URL — or a redirect target — resolves to a non-public
    address. Without this, /api/img is an open forwarder: it takes any URL
    from an unauthenticated request and fetches it server-side, which is
    exactly the shape of a request needed to reach the VM's own localhost
    services or GCP's internal network."""


def is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_host(host: str) -> None:
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

    Deliberately synchronous — socket.getaddrinfo() blocks, and keeping
    this a plain sync function (rather than an async one wrapping it
    internally) keeps it a pure, directly-unit-testable helper with no
    event-loop dependency. Callers in the async proxy_image handler below
    run it via asyncio.to_thread(); calling it directly there would block
    uvicorn's single event loop for the DNS lookup's duration on every
    image request — serializing what the browser intended to fetch in
    parallel, which is exactly what made image-heavy articles slow to
    open (found 2026-08-13, from a live report of a slow-loading article).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfBlocked(f"could not resolve host: {host}") from exc
    for _family, _type, _proto, _canon, sockaddr in infos:
        if not is_public_address(sockaddr[0]):
            raise SsrfBlocked(f"host {host!r} resolves to a non-public address")


def assert_safe_url(url: str) -> SplitResult:
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.hostname:
        raise SsrfBlocked(f"unsupported or invalid URL: {url}")
    assert_public_host(parts.hostname)
    return parts


class FetchTooLarge(Exception):
    """The response body exceeded safe_get's max_bytes."""


class SafeFetch(NamedTuple):
    url: str  # final URL, after redirects
    status: int
    content_type: str
    body: bytes


def safe_get(
    url: str,
    *,
    max_bytes: int = 3 * 1024 * 1024,
    timeout: float = 10.0,
    max_redirects: int = 3,
) -> SafeFetch:
    """Blocking GET of a request-supplied URL: every hop (the original URL
    and each redirect target) must pass assert_safe_url, and the body is
    streamed with a hard size cap. Returns the final response. Raises SsrfBlocked,
    FetchTooLarge or httpx.HTTPError. Call off the event loop."""
    current = url
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(max_redirects + 1):
            assert_safe_url(current)
            with client.stream(
                "GET",
                current,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            ) as resp:
                if resp.status_code in _REDIRECT_CODES:
                    location = resp.headers.get("Location")
                    if not location:
                        raise httpx.HTTPError(f"redirect without Location from {current}")
                    current = urljoin(current, location)
                    continue
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FetchTooLarge(f"page is larger than {max_bytes // (1024 * 1024)} MB")
                    chunks.append(chunk)
                return SafeFetch(
                    url=current,
                    status=resp.status_code,
                    content_type=resp.headers.get("Content-Type", ""),
                    body=b"".join(chunks),
                )
    raise httpx.TooManyRedirects(f"too many redirects fetching {url}")
