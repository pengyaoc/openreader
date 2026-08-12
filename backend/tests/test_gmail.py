"""Tests for the Gmail message parser. Pure function — given a Gmail API
message resource (the same shape messages.get(format='full') returns), it
must extract subject/from/date/body without any network I/O.
"""
import base64

from app.connectors.gmail import parse_message


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def make_message(
    *,
    message_id="18abc123",
    subject="Weekly Newsletter #42",
    from_header="Jane Doe <jane@newsletter.example.com>",
    date_header="Tue, 11 Aug 2026 05:30:24 +0000",
    html_body="<p>Hello <b>world</b></p>",
    plain_body=None,
    internal_date_ms="1786512624000",
):
    parts = []
    if plain_body is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(plain_body)}})
    if html_body is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html_body)}})

    return {
        "id": message_id,
        "internalDate": internal_date_ms,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": from_header},
                {"name": "Date", "value": date_header},
            ],
            "parts": parts,
        },
    }


def test_parses_subject_as_title():
    entry = parse_message(make_message(subject="Weekly Newsletter #42"))
    assert entry.title == "Weekly Newsletter #42"


def test_parses_from_header_as_author():
    # Display name only, not the raw "Name <email>" header — cleaner in the
    # UI's "By {author}" byline.
    entry = parse_message(make_message(from_header="Jane Doe <jane@newsletter.example.com>"))
    assert entry.author == "Jane Doe"


def test_falls_back_to_raw_from_header_when_no_display_name():
    entry = parse_message(make_message(from_header="jane@newsletter.example.com"))
    assert entry.author == "jane@newsletter.example.com"


def test_uses_message_id_as_guid():
    entry = parse_message(make_message(message_id="18abc123"))
    assert entry.guid == "18abc123"


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


def test_published_at_derived_from_internal_date():
    entry = parse_message(make_message(internal_date_ms="1754899824000"))
    assert entry.published_at is not None
    assert entry.published_at.startswith("2025-08-11")


def test_url_is_a_gmail_deep_link():
    entry = parse_message(make_message(message_id="18abc123"))
    assert "18abc123" in entry.url
    assert entry.url.startswith("https://mail.google.com/")


def test_handles_nested_multipart_mixed_with_attachment():
    # multipart/mixed wrapping multipart/alternative, as real Gmail messages
    # often are when they include attachments or inline images.
    inner = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("plain")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>nested html</p>")}},
        ],
    }
    message = {
        "id": "18xyz",
        "internalDate": "1754899824000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Has attachment"},
                {"name": "From", "value": "x@example.com"},
            ],
            "parts": [inner, {"mimeType": "application/pdf", "body": {"attachmentId": "abc"}}],
        },
    }
    entry = parse_message(message)
    assert "nested html" in entry.content_html
