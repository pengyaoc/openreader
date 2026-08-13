"""Tests for the IMAP connector's pure functions — parse_message() (raw
RFC822 bytes -> NormalizedEntry) and parse_query() (Gmail-search-style query
string -> from/subject/newer_than filters). No network, no imaplib socket
use.
"""
from email.message import EmailMessage

from app.connectors.imap import parse_message, parse_query


def make_message(
    *,
    message_id="<abc123@newsletter.example.com>",
    subject="Weekly Newsletter #42",
    from_header="Jane Doe <jane@newsletter.example.com>",
    date_header="Tue, 11 Aug 2026 05:30:24 +0000",
    html_body="<p>Hello <b>world</b></p>",
    plain_body=None,
) -> bytes:
    msg = EmailMessage()
    msg["Message-Id"] = message_id
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["Date"] = date_header

    if html_body is not None and plain_body is not None:
        msg.set_content(plain_body)
        msg.add_alternative(html_body, subtype="html")
    elif html_body is not None:
        msg.set_content(html_body, subtype="html")
    elif plain_body is not None:
        msg.set_content(plain_body)

    return bytes(msg)


def test_parses_subject_as_title():
    entry = parse_message(make_message(subject="Weekly Newsletter #42"))
    assert entry.title == "Weekly Newsletter #42"


def test_parses_from_header_as_author():
    entry = parse_message(make_message(from_header="Jane Doe <jane@newsletter.example.com>"))
    assert entry.author == "Jane Doe"


def test_falls_back_to_raw_from_header_when_no_display_name():
    entry = parse_message(make_message(from_header="jane@newsletter.example.com"))
    assert entry.author == "jane@newsletter.example.com"


def test_uses_message_id_header_as_guid():
    entry = parse_message(make_message(message_id="<abc123@newsletter.example.com>"))
    assert entry.guid == "<abc123@newsletter.example.com>"


def test_missing_message_id_falls_back_to_deterministic_hash():
    # make_message() always sets Message-Id, so build the bytes by hand to
    # actually omit the header and exercise the fallback path.
    msg = EmailMessage()
    msg["Subject"] = "No id here"
    msg["From"] = "x@example.com"
    msg["Date"] = "Tue, 11 Aug 2026 05:30:24 +0000"
    msg.set_content("body")
    raw = bytes(msg)

    entry1 = parse_message(raw)
    entry2 = parse_message(raw)
    assert entry1.guid == entry2.guid  # deterministic, not random
    assert entry1.guid.startswith("imap-")


def test_prefers_html_body_over_plain_text():
    entry = parse_message(
        make_message(html_body="<p>HTML version</p>", plain_body="Plain text version")
    )
    assert "HTML version" in entry.content_html
    assert "Plain text version" not in entry.content_html


def test_falls_back_to_plain_text_wrapped_in_pre_when_no_html():
    entry = parse_message(make_message(html_body=None, plain_body="Just plain text here."))
    assert "<pre>" in entry.content_html
    assert "Just plain text here." in entry.content_html


def test_published_at_derived_from_date_header():
    entry = parse_message(make_message(date_header="Tue, 11 Aug 2026 05:30:24 +0000"))
    assert entry.published_at is not None
    assert entry.published_at.startswith("2026-08-11")


def test_url_is_empty_no_public_deep_link_available():
    # Unlike Gmail's API (which hands back an id usable in a mail.google.com
    # deep link), plain IMAP has nothing equivalent — the frontend already
    # hides the "open original" link when url is falsy.
    entry = parse_message(make_message())
    assert entry.url == ""


def test_handles_multipart_mixed_with_attachment():
    msg = EmailMessage()
    msg["Message-Id"] = "<xyz@example.com>"
    msg["Subject"] = "Has attachment"
    msg["From"] = "x@example.com"
    msg.set_content("plain fallback")
    msg.add_alternative("<p>nested html</p>", subtype="html")
    msg.add_attachment(b"%PDF-fake", maintype="application", subtype="pdf", filename="a.pdf")

    entry = parse_message(bytes(msg))
    assert "nested html" in entry.content_html


def test_parse_query_extracts_from_filter():
    from_filter, subject_filter, newer_than = parse_query("from:sender@example.com newer_than:30d")
    assert from_filter == "sender@example.com"
    assert subject_filter is None
    assert newer_than == 30


def test_parse_query_extracts_quoted_subject_filter():
    from_filter, subject_filter, newer_than = parse_query('subject:"Weekly Digest"')
    assert subject_filter == "Weekly Digest"


def test_parse_query_handles_empty_or_none():
    assert parse_query(None) == (None, None, None)
    assert parse_query("") == (None, None, None)


def test_parse_query_ignores_unsupported_gmail_tokens():
    # IMAP SEARCH doesn't support Gmail's full operator set (label:, -,
    # etc.) — unrecognized tokens are silently ignored, not an error.
    from_filter, subject_filter, newer_than = parse_query("label:important -is:starred")
    assert from_filter is None
    assert subject_filter is None
    assert newer_than is None
