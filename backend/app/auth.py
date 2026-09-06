"""Login. As of 2026-09-05 this is trusted_header via the shared pchauth
library (see docs/WORKLOG.md and
docs/superpowers/specs/2026-09-05-consolidated-login-design.md in the
pchauth repo) — a shared Apache mod_auth_openidc gateway is the actual
OIDC client, and this app just reads the X-Remote-Email header it
injects. No cookie, no password, no session of this app's own any more.

The bcrypt/session-cookie functions below (verify_password through
clear_session_cookie) are the previous app-layer login (docs/WORKLOG.md,
2026-08-13 -> 2026-08-31), kept only as a documented rollback path while
the new login is verified live — nothing calls them any more, and they
are deleted once that verification is done (see docs/WORKLOG.md,
2026-09-05).

READER_AUTH_MODE unset (local/LAN default, matching every other optional
credential in this app — IMAP_HOST/_USER/_PASSWORD, READONLY_CONFIG) means
'off': every request is attributed to db.DEFAULT_USER_ID, reproducing the
old no-login behavior exactly. This is an app-level default, not pchauth's
own library default of 'required' — see load_auth_config below.
"""
from __future__ import annotations

import base64
import hmac
import os
import time
from hashlib import sha256

import bcrypt
from starlette.responses import Response

from app import settings
from app.db import DEFAULT_USER_ID, upsert_user_by_email
from app.pchauth.config import AuthConfig
from app.pchauth.config import load_config as load_pchauth_config

SESSION_COOKIE_NAME = "reader_session"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60  # 90 days, per user preference

# Compared against when the submitted username doesn't exist. Without it an
# unknown username returns in microseconds while a real one costs bcrypt's
# deliberate ~100-300ms, which is a username-enumeration oracle anyone can
# read off the network.
_DUMMY_HASH = bcrypt.hashpw(b"openreader-no-such-user", bcrypt.gensalt()).decode("ascii")


def load_auth_config() -> AuthConfig:
    """READER_AUTH_MODE unset means 'off' — the app's own default, not
    pchauth's library default of 'required' (which would otherwise demand
    READER_ALLOWED_EMAILS just to run locally). The cohosted deploy sets
    READER_AUTH_MODE=optional and a real READER_ALLOWED_EMAILS explicitly."""
    if os.environ.get("READER_AUTH_MODE"):
        return load_pchauth_config(os.environ, prefix="READER")
    return AuthConfig(mode="off")


def auth_configured() -> bool:
    """Whether this deployment requires a login at all."""
    return load_auth_config().mode != "off"


def upsert_user(identity, get_conn) -> int | None:
    """pchauth's upsert_user callback. The off-mode sentinel identity
    (empty email) maps straight to DEFAULT_USER_ID with no DB write at
    all — reproducing the old no-login behavior exactly, rather than
    creating a spurious empty-email account."""
    if not identity.email:
        return DEFAULT_USER_ID
    conn = get_conn()
    try:
        return upsert_user_by_email(conn, identity.email)
    finally:
        conn.close()


def verify_password(plain: str, password_hash: str | None) -> bool:
    """Bcrypt comparison against a hash from users.password_hash —
    deliberately ~100-300ms of CPU. Callers must run this via
    asyncio.to_thread, never inline in an async handler."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash in the DB — treat as "no valid credential", not a crash.
        return False


def create_session_token(user_id: int) -> str:
    """Payload is "<user_id>:<expiry>" and the HMAC covers both, so one
    account's id can't be swapped for another's without invalidating the
    signature. Both halves are digits around a fixed separator, so there's
    no concatenation ambiguity to exploit."""
    expires_at = int(time.time()) + SESSION_MAX_AGE_SECONDS
    payload = f"{user_id}:{expires_at}"
    return f"{payload}.{_sign(payload.encode('ascii'))}"


def verify_session_token(token: str | None) -> int | None:
    """Returns the authenticated account id, or None.

    Pre-multi-user cookies ("<expiry>.<sig>", no account id) fail the
    unpacking below and are rejected rather than attributed to the default
    account — silently mapping them to user 1 would make the only thing
    separating the two accounts bypassable by holding an old cookie. The
    cost is one extra login for anyone carrying one.
    """
    if not token or not settings.SESSION_SECRET:
        return None
    try:
        payload_str, signature = token.split(".", 1)
        user_id_str, expires_str = payload_str.split(":", 1)
        user_id = int(user_id_str)
        expires_at = int(expires_str)
    except ValueError:
        return None

    expected_signature = _sign(payload_str.encode("ascii"))
    if not hmac.compare_digest(signature, expected_signature):
        return None
    if time.time() >= expires_at:
        return None
    return user_id


def _sign(payload: bytes) -> str:
    digest = hmac.new(settings.SESSION_SECRET.encode("utf-8"), payload, sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(user_id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
