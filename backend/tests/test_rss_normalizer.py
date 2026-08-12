"""Tests for the feed normalizer against real-shaped fixtures.

Covers Atom, RSS 2.0, RSS 1.0/RDF, truncated content, missing guid,
malformed XML, and a billion-laughs entity-expansion attempt.
"""
from pathlib import Path

import pytest

from app.connectors.rss import FeedParseError, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_atom_feed_parses_two_entries():
    entries = parse_feed(load("atom.xml"))
    assert len(entries) == 2
    first = entries[0]
    assert first.guid == "tag:example.com,2026:post-1"
    assert first.title == "First Atom Post"
    assert first.url == "https://example.com/atom/1"
    assert first.author == "Jane Doe"
    assert "2026-08-01" in first.published_at
    assert "Full" in first.content_html
    assert "content" in first.content_html


def test_atom_entry_without_published_falls_back_to_updated():
    entries = parse_feed(load("atom.xml"))
    second = entries[1]
    assert second.published_at is not None
    assert "2026-08-02" in second.published_at


def test_atom_entry_without_content_uses_summary():
    entries = parse_feed(load("atom.xml"))
    second = entries[1]
    assert second.content_html == "" or second.content_html is None
    assert second.summary == "Only a summary, no published date."


def test_rss2_feed_parses_two_items():
    entries = parse_feed(load("rss2.xml"))
    assert len(entries) == 2
    first = entries[0]
    assert first.guid == "rss-guid-1"
    assert first.title == "First RSS Post"
    assert first.url == "https://example.com/rss/1"
    assert "content:encoded" not in first.content_html
    assert "Full" in first.content_html


def test_rss2_prefers_content_encoded_over_description():
    entries = parse_feed(load("rss2.xml"))
    first = entries[0]
    assert "Full <b>content</b>" in first.content_html


def test_rss2_item_missing_guid_falls_back_to_link():
    entries = parse_feed(load("rss2.xml"))
    second = entries[1]
    assert second.guid == "https://example.com/rss/2"


def test_rss2_truncated_item_has_summary_but_no_full_content():
    entries = parse_feed(load("rss2.xml"))
    second = entries[1]
    assert second.summary == "Just a teaser."
    assert not second.content_html


def test_rdf_rss1_feed_parses_one_item():
    entries = parse_feed(load("rdf.xml"))
    assert len(entries) == 1
    item = entries[0]
    assert item.title == "First RDF Item"
    assert item.url == "https://example.com/rdf/1"
    assert item.summary == "An RDF/RSS 1.0 item description."


def test_malformed_xml_raises_feed_parse_error():
    with pytest.raises(FeedParseError):
        parse_feed(load("malformed.xml"))


def test_entity_expansion_attempt_is_rejected_not_expanded():
    # defusedxml should refuse entity expansion outright rather than silently
    # inlining it — either raises, or the entity is left unexpanded.
    try:
        entries = parse_feed(load("entity_expansion.xml"))
    except FeedParseError:
        return  # rejecting the whole doc is an acceptable outcome
    # If it didn't raise, it must not have expanded the entity bomb.
    for entry in entries:
        assert "lollollol" not in (entry.title or "")
