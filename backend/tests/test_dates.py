"""Tests for feed date normalization. Every feed date format must resolve
to the same sortable ISO-8601 UTC representation, or ORDER BY published_at
sorts garbage once RSS (RFC822) and Atom (ISO8601) sources mix in one list.
"""
from app.connectors.dates import parse_date


def test_parses_rfc822_rss_pubdate():
    result = parse_date("Tue, 11 Aug 2026 05:30:24 +0000")
    assert result == "2026-08-11T05:30:24+00:00"


def test_parses_iso8601_atom_date_with_z_suffix():
    result = parse_date("2026-08-01T12:00:00Z")
    assert result == "2026-08-01T12:00:00+00:00"


def test_parses_iso8601_with_explicit_offset():
    result = parse_date("2026-08-01T12:00:00+08:00")
    assert result == "2026-08-01T04:00:00+00:00"


def test_rfc822_and_iso8601_for_the_same_instant_produce_identical_strings():
    rfc822 = parse_date("Sat, 01 Aug 2026 12:00:00 GMT")
    iso = parse_date("2026-08-01T12:00:00Z")
    assert rfc822 == iso


def test_returns_none_for_empty_or_unparseable_input():
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("not a date") is None


def test_sorting_normalized_dates_as_strings_matches_chronological_order():
    dates = [
        parse_date("Sat, 01 Aug 2026 12:00:00 GMT"),  # earliest
        parse_date("2026-08-05T00:00:00Z"),
        parse_date("Tue, 11 Aug 2026 05:30:24 +0000"),  # latest
    ]
    assert sorted(dates) == dates
