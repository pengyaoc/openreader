"""IMAP newsletter connector — reads a dedicated newsletter-only mailbox,
authenticated with an app password rather than OAuth (no re-auth needed,
unlike a short-lived OAuth grant). Read-only throughout: SEARCH and FETCH
only, in `readonly=True` mode, never STORE/EXPUNGE/DELETE.

Query field (`from:x subject:y newer_than:Nd`) mirrors Gmail's own search
syntax for familiarity — see parse_query(). IMAP SEARCH doesn't support
Gmail's full operator set, so only those three tokens are understood.

Credential handling is deliberately narrow:
  - connect() always uses implicit TLS (IMAP4_SSL) with a verified default
    SSL context — never plaintext + STARTTLS, which has a pre-TLS window.
  - imaplib's debug output echoes the LOGIN command, password included, to
    stderr. Debug stays at its default of 0 everywhere in this module, and
    login failures are wrapped in ImapAuthError with a fixed message —
    the underlying imaplib exception (which can contain the command) is
    chained via `from exc` for local debugging but its str() is never
    surfaced to callers/logs by this module.
"""
from __future__ import annotations

import hashlib
import imaplib
import re
import ssl
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr

from app.connectors.base import NormalizedEntry, plain_text_to_html
from app.connectors.dates import parse_date

DEFAULT_PORT = 993
DEFAULT_FOLDER = "INBOX"


class ImapError(Exception):
    """Connection, login, SEARCH, or FETCH failure. Never carries the raw
    underlying exception text in str() — see module docstring."""


def connect(
    host: str, user: str, password: str, *, port: int = DEFAULT_PORT, timeout: float = 30.0
) -> imaplib.IMAP4_SSL:
    """Opens an implicit-TLS connection and logs in. Caller owns the
    returned client's lifetime (close/logout).

    imaplib's socket has no timeout by default, so a server that accepts
    the connection and then stops responding mid-command leaves the caller
    blocked forever (see docs/WORKLOG.md 2026-08-13). `timeout` applies to
    the socket for the connection's whole lifetime, bounding SEARCH/FETCH
    too, not just connect/login."""
    context = ssl.create_default_context()
    try:
        client = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=timeout)
        client.login(user, password)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ImapError("IMAP connect/login failed — check host/user/app password") from exc
    return client


def _imap_date(dt: datetime) -> str:
    """IMAP SEARCH SINCE takes a date, not a datetime — DD-Mon-YYYY."""
    return dt.strftime("%d-%b-%Y")


def search_message_ids(
    client: imaplib.IMAP4_SSL,
    folder: str = DEFAULT_FOLDER,
    *,
    since: datetime | None = None,
    from_filter: str | None = None,
    subject_filter: str | None = None,
) -> list[str]:
    status, _ = client.select(folder, readonly=True)
    if status != "OK":
        raise ImapError(f"could not select IMAP folder {folder!r}")

    criteria: list[str] = []
    if since is not None:
        criteria += ["SINCE", _imap_date(since)]
    if from_filter:
        criteria += ["FROM", f'"{from_filter}"']
    if subject_filter:
        criteria += ["SUBJECT", f'"{subject_filter}"']
    if not criteria:
        criteria = ["ALL"]

    status, data = client.search(None, *criteria)
    if status != "OK":
        raise ImapError("IMAP SEARCH failed")
    if not data or not data[0]:
        return []
    return [msg_id.decode("ascii") for msg_id in data[0].split()]


def fetch_message(client: imaplib.IMAP4_SSL, message_id: str) -> bytes:
    status, data = client.fetch(message_id, "(RFC822)")
    if status != "OK" or not data or data[0] is None:
        raise ImapError(f"IMAP FETCH failed for message {message_id!r}")
    return data[0][1]


_QUERY_FROM_RE = re.compile(r"from:(\S+)")
_QUERY_SUBJECT_RE = re.compile(r'subject:"([^"]+)"|subject:(\S+)')
_QUERY_NEWER_THAN_RE = re.compile(r"newer_than:(\d+)d")


def parse_query(query: str | None) -> tuple[str | None, str | None, int | None]:
    """Extracts (from_filter, subject_filter, newer_than_days) from a
    Gmail-search-style query string. Any token this doesn't recognize
    (e.g. `after:`, `-label:`) is silently ignored — same fail-open
    behavior as an IMAP SEARCH that just doesn't narrow on it."""
    query = query or ""
    from_match = _QUERY_FROM_RE.search(query)
    subject_match = _QUERY_SUBJECT_RE.search(query)
    newer_than_match = _QUERY_NEWER_THAN_RE.search(query)

    subject = None
    if subject_match:
        subject = subject_match.group(1) or subject_match.group(2)

    return (
        from_match.group(1) if from_match else None,
        subject,
        int(newer_than_match.group(1)) if newer_than_match else None,
    )


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    decoded = []
    for text, encoding in decode_header(value):
        if isinstance(text, bytes):
            decoded.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _find_body(msg: Message) -> tuple[str | None, str | None]:
    """Walks a (possibly multipart) message and returns (html, plain)."""
    if not msg.is_multipart():
        content_type = msg.get_content_type()
        if content_type == "text/html":
            return _decode_payload(msg), None
        if content_type == "text/plain":
            return None, _decode_payload(msg)
        return None, None

    html, plain = None, None
    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type == "text/html" and html is None:
            html = _decode_payload(part)
        elif content_type == "text/plain" and plain is None:
            plain = _decode_payload(part)
    return html, plain


def _fallback_guid(msg: Message) -> str:
    """A message missing Message-Id is rare but not impossible. Hash a few
    stable headers so the guid is at least deterministic across refreshes
    (matters for dedup — a random guid would re-insert the same message on
    every run)."""
    basis = "|".join(
        [msg.get("Date", ""), msg.get("Subject", ""), msg.get("From", "")]
    ).encode("utf-8", errors="replace")
    return "imap-" + hashlib.sha256(basis).hexdigest()[:16]


def _parse(raw: bytes) -> tuple[Message, NormalizedEntry]:
    msg = message_from_bytes(raw)

    message_id = _decode_header_value(msg.get("Message-Id")) or _fallback_guid(msg)
    subject = _decode_header_value(msg.get("Subject"))
    from_header = _decode_header_value(msg.get("From"))
    author_name = parseaddr(from_header)[0] or from_header

    html, plain = _find_body(msg)
    if html:
        content_html = html
    elif plain:
        content_html = plain_text_to_html(plain)
    else:
        content_html = ""

    published_at = parse_date(msg.get("Date"))

    entry = NormalizedEntry(
        guid=message_id,
        url="",
        title=subject,
        author=author_name,
        published_at=published_at,
        summary="",
        content_html=content_html,
    )
    return msg, entry


def parse_message(raw: bytes) -> NormalizedEntry:
    """Pure — no network. Extracts a NormalizedEntry from a raw RFC822
    message. `url` is deliberately left empty: unlike Gmail's API, which
    hands back an internal id usable in a mail.google.com deep link, plain
    IMAP has no equivalent — the frontend already hides the 'open original'
    link when url is falsy."""
    _, entry = _parse(raw)
    return entry


def parse_message_with_from_header(raw: bytes) -> tuple[NormalizedEntry, str]:
    """Like parse_message, but also returns the decoded From header
    verbatim (display name + address, undecomposed) — needed when routing
    one shared SEARCH/FETCH pass across several sources' own from:/subject:
    query tokens locally (see ingest/refresh.py), since IMAP's own FROM
    SEARCH matches the whole header, not just the display name
    NormalizedEntry.author narrows to via parseaddr."""
    msg, entry = _parse(raw)
    return entry, _decode_header_value(msg.get("From"))


def matches_query(
    from_header: str, subject: str, from_filter: str | None, subject_filter: str | None
) -> bool:
    """Local equivalent of the FROM/SUBJECT criteria search_message_ids
    would otherwise apply server-side — same semantics IMAP SEARCH uses:
    case-insensitive substring, both required (AND) when both are given.
    A filter that's None/empty always passes (nothing to narrow on)."""
    if from_filter and from_filter.lower() not in (from_header or "").lower():
        return False
    if subject_filter and subject_filter.lower() not in (subject or "").lower():
        return False
    return True


__all__: list[str] = [
    "ImapError",
    "connect",
    "search_message_ids",
    "fetch_message",
    "parse_query",
    "parse_message",
    "DEFAULT_FOLDER",
]
