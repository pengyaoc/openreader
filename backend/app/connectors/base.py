"""Common shape every source connector normalizes into. Connectors know
nothing about rules, dedup, or persistence — they only produce these."""
from __future__ import annotations

import re

import msgspec


class NormalizedEntry(msgspec.Struct, kw_only=True):
    guid: str
    url: str
    title: str
    author: str = ""
    published_at: str | None = None
    summary: str = ""
    content_html: str = ""


def plain_text_to_html(plain: str) -> str:
    """Wraps a plain-text message body in <pre> so whitespace is preserved
    literally, collapsing 3+ blank lines to 2 first — newsletters (Gmail
    and IMAP alike) often pad sections with several blank lines, which
    otherwise reads as a wall of gaps once whitespace stops collapsing."""
    return f"<pre>{re.sub(r'\n{3,}', '\n\n', plain)}</pre>"
