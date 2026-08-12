"""Normalizes feed-provided dates to a single sortable ISO-8601 UTC string.

Without this, RSS 2.0's RFC822 pubDate ("Tue, 11 Aug 2026 05:30:24 +0000")
and Atom's ISO-8601 ("2026-08-01T12:00:00Z") get stored as raw strings and
`ORDER BY published_at` does a lexicographic string comparison — which only
sorts correctly within one format. Mix RSS and Atom sources in one list and
the order is garbage. Every connector must normalize through here before
handing published_at to the persistence layer.
"""
from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime


def parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()

    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        dt = None

    if dt is None:
        try:
            dt = _parse_iso8601(raw)
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse_iso8601(raw: str):
    from datetime import datetime

    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)
