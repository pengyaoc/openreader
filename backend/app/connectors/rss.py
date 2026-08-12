"""RSS/Atom/RDF feed parsing and normalization.

Uses defusedxml.ElementTree (C-accelerated, entity-expansion-safe) instead
of feedparser — see the design doc's dependency table. Handles the three
feed shapes actually seen in the wild: Atom, RSS 2.0 (with optional
content:encoded), and RSS 1.0/RDF.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from app.connectors.base import NormalizedEntry
from app.connectors.dates import parse_date

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class FeedParseError(Exception):
    """Raised when a feed document can't be parsed at all — malformed XML,
    an entity-expansion attempt, or an unrecognized root element."""


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_atom(root: ET.Element) -> list[NormalizedEntry]:
    entries = []
    for entry in root.findall("atom:entry", NS):
        guid = _text(entry.find("atom:id", NS))
        title = _text(entry.find("atom:title", NS))
        link_el = entry.find("atom:link[@rel='alternate']", NS)
        if link_el is None:
            link_el = entry.find("atom:link", NS)
        url = link_el.get("href", "") if link_el is not None else ""
        author_el = entry.find("atom:author/atom:name", NS)
        author = _text(author_el)
        published = _text(entry.find("atom:published", NS)) or _text(
            entry.find("atom:updated", NS)
        )
        summary = _text(entry.find("atom:summary", NS))
        content_el = entry.find("atom:content", NS)
        content_html = _text(content_el)

        entries.append(
            NormalizedEntry(
                guid=guid or url,
                url=url,
                title=title,
                author=author,
                published_at=parse_date(published),
                summary=summary,
                content_html=content_html,
            )
        )
    return entries


def _parse_rss2(root: ET.Element) -> list[NormalizedEntry]:
    entries = []
    for item in root.findall(".//item"):
        title = _text(item.find("title"))
        url = _text(item.find("link"))
        guid_el = item.find("guid")
        guid = _text(guid_el) or url
        author = _text(item.find("author"))
        published = _text(item.find("pubDate"))
        summary = _text(item.find("description"))
        content_el = item.find("content:encoded", NS)
        content_html = _text(content_el)

        entries.append(
            NormalizedEntry(
                guid=guid,
                url=url,
                title=title,
                author=author,
                published_at=parse_date(published),
                summary=summary,
                content_html=content_html,
            )
        )
    return entries


def _parse_rdf(root: ET.Element) -> list[NormalizedEntry]:
    entries = []
    for item in root.findall("rss1:item", NS):
        title = _text(item.find("rss1:title", NS))
        url = _text(item.find("rss1:link", NS)) or item.get(
            f"{{{NS['rdf']}}}about", ""
        )
        published = _text(item.find("dc:date", NS))
        summary = _text(item.find("rss1:description", NS))

        entries.append(
            NormalizedEntry(
                guid=url,
                url=url,
                title=title,
                published_at=parse_date(published),
                summary=summary,
                content_html="",
            )
        )
    return entries


def parse_feed(raw: bytes) -> list[NormalizedEntry]:
    """Parse a feed document (Atom, RSS 2.0, or RSS 1.0/RDF) into
    normalized entries. Raises FeedParseError on malformed XML or an
    entity-expansion attempt — never silently returns partial garbage."""
    try:
        root = DET.fromstring(raw)
    except DefusedXmlException as exc:
        raise FeedParseError(f"rejected (entity expansion or similar): {exc}") from exc
    except ET.ParseError as exc:
        raise FeedParseError(f"malformed XML: {exc}") from exc

    tag = root.tag
    if tag == f"{{{NS['atom']}}}feed":
        return _parse_atom(root)
    if tag == f"{{{NS['rdf']}}}RDF":
        return _parse_rdf(root)
    if tag == "rss":
        return _parse_rss2(root)

    raise FeedParseError(f"unrecognized feed root element: {tag}")
