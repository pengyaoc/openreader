"""App-layer login, replacing the Apache Basic Auth this app used to sit
behind (docs/WORKLOG.md, 2026-08-13 -> 2026-08-13 cont.). Basic Auth's
`WWW-Authenticate` popup is invisible to Chrome's password-manager save
UI (that only hooks real `<form>` submissions) and its credential cache is
an in-memory, browser-session/tab-lifetime thing mobile Chrome discards
aggressively — both fixed by moving login into the app itself.

Accounts live in the `users` table as of 2026-08-31 (multi-user, see
docs/WORKLOG.md); `READER_AUTH_PASSWORD_HASH` is now only a seed for the
very first account on a brand-new DB, and passwords are managed with
`python -m app.useradm`. `verify_password` checks a submitted password
against a hash read from that table; a successful login gets a stateless,
HMAC-signed session cookie (`READER_SESSION_SECRET`) carrying the account
id and an expiry — no session table, nothing to garbage-collect, logout is
just letting the client drop the cookie.

Permissive by default, like every other optional credential in this app
(IMAP_HOST/_USER/_PASSWORD, READONLY_CONFIG): with SESSION_SECRET unset,
AuthMiddleware doesn't gate anything and every request is attributed to
db.DEFAULT_USER_ID — local/LAN use has never required a login, only the
internet-facing VM deployment opts into one.

Whether a login is required keys off SESSION_SECRET *alone*. It used to
take both env vars, with "exactly one set" failing closed as a probable
deploy typo; that pairing no longer exists, and keeping AUTH_PASSWORD_HASH
in the condition would have been worse than losing it — that variable is
consumed once and then dead, so deleting the now-useless line from the env
file would silently unlock an internet-facing deployment. main.py warns
loudly at startup instead when a multi-account DB comes up with no secret.
"""
from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256

import bcrypt
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app import settings
from app.db import DEFAULT_USER_ID

SESSION_COOKIE_NAME = "reader_session"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60  # 90 days, per user preference

# Routes reachable with no session at all: logging in, logging out (a
# no-op if there's nothing to clear), and the SPA shell itself — the
# login screen has to load before the user is authenticated. /api/me is
# deliberately *not* here: its 401 is what tells the frontend to show the
# login screen.
_UNPROTECTED_PATHS = {"/api/login", "/api/logout"}

# Compared against when the submitted username doesn't exist. Without it an
# unknown username returns in microseconds while a real one costs bcrypt's
# deliberate ~100-300ms, which is a username-enumeration oracle anyone can
# read off the network.
_DUMMY_HASH = bcrypt.hashpw(b"openreader-no-such-user", bcrypt.gensalt()).decode("ascii")


def auth_configured() -> bool:
    """Whether this deployment requires a login. See the module docstring
    for why this is SESSION_SECRET alone."""
    return bool(settings.SESSION_SECRET)


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


class AuthMiddleware:
    """Pure ASGI middleware, checked ahead of every /api/* route except
    login/logout. No WWW-Authenticate header on the 401 — that header is
    what makes a browser pop its native Basic Auth dialog; omitting it
    means the SPA's own fetch code just sees a normal 401 body instead."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if scope["path"] in _UNPROTECTED_PATHS:
            await self.app(scope, receive, send)
            return

        if not auth_configured():
            # Local/LAN default: no login required. Requests still need an
            # identity for the per-user read/starred model to have an
            # owner, and it's the same account api/_common.current_user_id
            # falls back to, so both paths agree.
            _set_user(scope, DEFAULT_USER_ID)
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        user_id = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME))
        if user_id is None:
            response = JSONResponse({"error": "not authenticated"}, status_code=401)
            await response(scope, receive, send)
            return

        _set_user(scope, user_id)
        await self.app(scope, receive, send)


def _set_user(scope: Scope, user_id: int) -> None:
    """Starlette builds request.state out of scope["state"] (a plain dict
    it creates lazily), so writing the key here is what makes
    request.state.user_id visible to every downstream handler — no extra
    plumbing, and nothing to keep in sync per route."""
    scope.setdefault("state", {})["user_id"] = user_id
