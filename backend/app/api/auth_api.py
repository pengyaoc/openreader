from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api._common import current_user_id
from app.auth import auth_configured


async def me(request: Request) -> JSONResponse:
    """Who the current request belongs to. There is no /api/login or
    /api/logout any more — Apache owns login entirely in trusted_header
    mode (see docs/WORKLOG.md, 2026-09-05) — this is now the frontend's
    only sign-in-state probe.

    In `optional` mode an anonymous request is not an error: it means
    nobody is signed in, and the frontend renders a signed-out browsing
    state rather than a forced login screen. `required` mode never
    reaches this handler anonymously — PchauthMiddleware 401s it first.

    `auth_enabled` is false on a no-login (`off` mode) deployment, where
    the identity below is the default account rather than anyone who
    signed in.
    """
    user_id = current_user_id(request)
    if user_id is None:
        return JSONResponse({"id": None, "username": None, "auth_enabled": auth_configured()})

    conn = request.app.state.get_conn()
    row = conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        # A trusted identity for an account that no longer exists.
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return JSONResponse(
        {"id": row[0], "username": row[1], "auth_enabled": auth_configured()}
    )
