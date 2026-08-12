"""Thin conditional-GET wrapper shared by connectors that fetch over HTTP.

Not unit-tested against a live network (that's what the refresh smoke test
and manual verification are for) — this module is intentionally tiny so the
untested surface area stays small.
"""
from __future__ import annotations

import httpx

USER_AGENT = "reader/0.1 (+https://github.com/pengyao/reader)"


class FetchResult:
    __slots__ = ("status", "body", "etag", "last_modified")

    def __init__(
        self,
        status: int,
        body: bytes | None,
        etag: str | None,
        last_modified: str | None,
    ):
        self.status = status
        self.body = body
        self.etag = etag
        self.last_modified = last_modified


def conditional_get(
    client: httpx.Client,
    url: str,
    etag: str | None,
    last_modified: str | None,
    timeout: float = 10.0,
) -> FetchResult:
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    resp = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)

    if resp.status_code == 304:
        return FetchResult(304, None, etag, last_modified)

    resp.raise_for_status()
    return FetchResult(
        resp.status_code,
        resp.content,
        resp.headers.get("ETag"),
        resp.headers.get("Last-Modified"),
    )
