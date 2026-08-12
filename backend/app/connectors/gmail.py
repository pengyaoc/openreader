"""Gmail newsletter connector. Read-only throughout — this module only ever
calls messages.list, messages.get, and history.list. It never sends,
labels, drafts, or deletes anything, matching the read-only Gmail policy.

Auth is handled outside this module: scripts/gmail_auth.py performs a
one-time OAuth desktop-app consent (the only place google-auth-oauthlib is
imported) and writes data/token.json. At runtime, refresh_access_token()
does a single POST to swap the refresh token for a fresh access token —
no Google client library needed for that, it's one HTTP call.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path

import httpx

from app.connectors.base import NormalizedEntry
from app.connectors.dates import parse_date

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def list_message_ids(access_token: str, query: str, page_size: int = 50) -> list[str]:
    resp = httpx.get(
        f"{API_BASE}/messages",
        params={"q": query, "maxResults": page_size},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("messages", [])]


def get_message(access_token: str, message_id: str) -> dict:
    resp = httpx.get(
        f"{API_BASE}/messages/{message_id}",
        params={"format": "full"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def access_token_from_token_file(token_path: Path) -> str | None:
    """Reads data/token.json (written by scripts/gmail_auth.py) and swaps
    the stored refresh token for a fresh access token. Returns None if the
    file doesn't exist — that's the normal state for anyone not using the
    Gmail connector, not an error."""
    if not token_path.exists():
        return None
    data = json.loads(token_path.read_text())
    return refresh_access_token(data["client_id"], data["client_secret"], data["refresh_token"])


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _find_body(payload: dict) -> tuple[str | None, str | None]:
    """Walks a (possibly nested multipart) message payload and returns
    (html, plain) — either may be None if that part type isn't present."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/html" and "data" in payload.get("body", {}):
        return _decode(payload["body"]["data"]), None
    if mime_type == "text/plain" and "data" in payload.get("body", {}):
        return None, _decode(payload["body"]["data"])

    html, plain = None, None
    for part in payload.get("parts", []):
        part_html, part_plain = _find_body(part)
        html = html or part_html
        plain = plain or part_plain
    return html, plain


def parse_message(raw: dict) -> NormalizedEntry:
    """Pure — no network. Extracts a NormalizedEntry from a Gmail API
    message resource (messages.get(format='full') response shape)."""
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])
    message_id = raw["id"]

    subject = _header(headers, "Subject")
    from_header = _header(headers, "From")
    author_name = parseaddr(from_header)[0] or from_header

    html, plain = _find_body(payload)
    if html:
        content_html = html
    elif plain:
        content_html = f"<pre>{plain}</pre>"
    else:
        content_html = ""

    internal_date = raw.get("internalDate")
    if internal_date:
        published_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC).isoformat()
    else:
        published_at = parse_date(_header(headers, "Date"))

    return NormalizedEntry(
        guid=message_id,
        url=f"https://mail.google.com/mail/u/0/#inbox/{message_id}",
        title=subject,
        author=author_name,
        published_at=published_at,
        summary="",
        content_html=content_html,
    )


__all__: list[str] = [
    "refresh_access_token",
    "list_message_ids",
    "get_message",
    "parse_message",
]
