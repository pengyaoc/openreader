"""App-layer login, replacing the Apache Basic Auth this app used to sit
behind (docs/WORKLOG.md, 2026-08-13 -> 2026-08-13 cont.). Basic Auth's
`WWW-Authenticate` popup is invisible to Chrome's password-manager save
UI (that only hooks real `<form>` submissions) and its credential cache is
an in-memory, browser-session/tab-lifetime thing mobile Chrome discards
aggressively — both fixed by moving login into the app itself.

Single shared password, no accounts (this app has no multi-user concept
anywhere else either): `verify_password` checks a bcrypt hash from
`READER_AUTH_PASSWORD_HASH`; a successful login gets a stateless,
HMAC-signed session cookie (`READER_SESSION_SECRET`) valid for
SESSION_MAX_AGE_SECONDS — no session table, nothing to garbage-collect,
logout is just letting the client drop the cookie.

Permissive by default, like every other optional credential in this app
(IMAP_HOST/_USER/_PASSWORD, READONLY_CONFIG): with both env vars unset,
AuthMiddleware doesn't gate anything — local/LAN use has never required a
login, only the internet-facing VM deployment opts into one. A *partial*
config (exactly one of the two set) fails closed instead of falling back
to permissive — that state looks like a deploy mistake, not an
intentional "no auth" choice, and silently staying open would be the
wrong failure mode for a typo in the one thing standing in for Basic Auth
on that deployment.
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

SESSION_COOKIE_NAME = "reader_session"
SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60  # 90 days, per user preference

# Routes reachable with no session at all: logging in, logging out (a
# no-op if there's nothing to clear), and the SPA shell itself — the
# login screen has to load before the user is authenticated.
_UNPROTECTED_PATHS = {"/api/login", "/api/logout"}


def verify_password(plain: str) -> bool:
    """Bcrypt comparison — deliberately ~100-300ms of CPU. Callers must
    run this via asyncio.to_thread, never inline in an async handler."""
    if not settings.AUTH_PASSWORD_HASH:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), settings.AUTH_PASSWORD_HASH.encode("utf-8"))
    except ValueError:
        # Malformed hash in config — treat as "no valid credential", not a crash.
        return False


def create_session_token() -> str:
    expires_at = int(time.time()) + SESSION_MAX_AGE_SECONDS
    payload = str(expires_at).encode("ascii")
    signature = _sign(payload)
    return f"{payload.decode('ascii')}.{signature}"


def verify_session_token(token: str | None) -> bool:
    if not token or not settings.SESSION_SECRET:
        return False
    try:
        payload_str, signature = token.split(".", 1)
        expires_at = int(payload_str)
    except ValueError:
        return False

    expected_signature = _sign(payload_str.encode("ascii"))
    if not hmac.compare_digest(signature, expected_signature):
        return False
    return time.time() < expires_at


def _sign(payload: bytes) -> str:
    digest = hmac.new(settings.SESSION_SECRET.encode("utf-8"), payload, sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def set_session_cookie(response: Response) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(),
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

        hash_set = bool(settings.AUTH_PASSWORD_HASH)
        secret_set = bool(settings.SESSION_SECRET)
        if not hash_set and not secret_set:
            await self.app(scope, receive, send)
            return
        if hash_set != secret_set:
            response = JSONResponse({"error": "auth misconfigured"}, status_code=401)
            await response(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not verify_session_token(token):
            response = JSONResponse({"error": "not authenticated"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
