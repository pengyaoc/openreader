"""Small pieces shared by two or more API route modules — not a general
dumping ground, just the handful of things that would otherwise be
copy-pasted per route file."""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
from app.config import Config, to_yaml
from app.db import DEFAULT_USER_ID

_MISSING = object()


class AnonymousUserError(Exception):
    """Raised by require_user_id() for a write attempted with no signed-in
    user — callers catch this and return a 401, never let it propagate as
    a 500."""


def current_user_id(request: Request) -> int | None:
    """The account this request reads as, or None for an anonymous request
    under optional mode. Distinct from require_user_id(): this is safe to
    call from any read path, including ones that should render fine for a
    signed-out visitor.

    AuthMiddleware puts user_id in request.state (see app/auth.py). The
    _MISSING fallback covers the one case where that middleware isn't in
    the stack at all: create_app(require_auth=False), which is how the
    test suite reaches every endpoint — treated the same as `off` mode,
    both attributing every request to DEFAULT_USER_ID.
    """
    value = getattr(request.state, "user_id", _MISSING)
    return DEFAULT_USER_ID if value is _MISSING else value


def require_user_id(request: Request) -> int:
    """The account this request must belong to for a write to proceed.
    Raises AnonymousUserError — never returns None — so a route handler
    that forgets to check the return value fails loudly (an exception the
    app's error handler turns into a 500 during development) rather than
    silently writing under None or DEFAULT_USER_ID.
    """
    user_id = current_user_id(request)
    if user_id is None:
        raise AnonymousUserError()
    return user_id


def readonly_response() -> JSONResponse | None:
    """Returns a 403 if config writes are locked on this deployment, else
    None — call at the top of any config-mutating endpoint before touching
    disk."""
    if settings.READONLY_CONFIG:
        return JSONResponse(
            {"error": "config is read-only on this deployment"}, status_code=403
        )
    return None


def save_config(request: Request, new_config: Config) -> None:
    """Writes a new Config to feeds.yaml and swaps it into app state — the
    two steps every config-mutating endpoint performs after validating its
    change, always together."""
    request.app.state.config_path.write_text(to_yaml(new_config))
    request.app.state.config = new_config
