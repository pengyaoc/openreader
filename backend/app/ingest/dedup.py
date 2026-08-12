"""Canonical URL + content-hash dedup. Pure — no DB access.

The DB layer is responsible for the actual dedup check (does this
content_hash already exist); this module only computes deterministic keys.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.lower()

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
        and k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(kept)

    path = parts.path
    if path.endswith("/") and path != "/":
        path = path[:-1]

    return urlunsplit((parts.scheme, host, path, query, ""))


def content_hash(url: str, title: str) -> str:
    canonical = canonicalize_url(url)
    normalized_title = re.sub(r"\s+", " ", title).strip().lower()
    payload = f"{canonical}|{normalized_title}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
