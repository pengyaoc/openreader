"""Common shape every source connector normalizes into. Connectors know
nothing about rules, dedup, or persistence — they only produce these."""
from __future__ import annotations

import msgspec


class NormalizedEntry(msgspec.Struct, kw_only=True):
    guid: str
    url: str
    title: str
    author: str = ""
    published_at: str | None = None
    summary: str = ""
    content_html: str = ""
