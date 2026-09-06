"""Starlette adapter for pchauth. Modeled directly on
reader/backend/app/auth.py:AuthMiddleware — same scope["state"]["user_id"]
mechanism, same deliberate omission of WWW-Authenticate on the 401 so no
native browser dialog fires; the SPA's own fetch code just sees a normal
401 body.
"""
from __future__ import annotations

from typing import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import AuthConfig
from .core import Identity, is_allowed
from .trusted_header import identity_from_headers

_OFF_MODE_IDENTITY = Identity(email="")


class PchauthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        config: AuthConfig,
        upsert_user: Callable[[Identity], int | None],
        protected_prefix: str = "/api/",
    ) -> None:
        self.app = app
        self.config = config
        self.upsert_user = upsert_user
        self.protected_prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.protected_prefix):
            await self.app(scope, receive, send)
            return

        if self.config.mode == "off":
            _set_user(scope, self.upsert_user(_OFF_MODE_IDENTITY))
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        identity = identity_from_headers(request.headers, self.config)

        if identity is None:
            if self.config.mode == "optional":
                _set_user(scope, None)
                await self.app(scope, receive, send)
                return
            response = JSONResponse({"error": "not authenticated"}, status_code=401)
            await response(scope, receive, send)
            return

        if not is_allowed(identity.email, self.config):
            response = JSONResponse({"error": "not on the allowlist"}, status_code=403)
            await response(scope, receive, send)
            return

        _set_user(scope, self.upsert_user(identity))
        await self.app(scope, receive, send)


def _set_user(scope: Scope, user_id: int | None) -> None:
    scope.setdefault("state", {})["user_id"] = user_id
