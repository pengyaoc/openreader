import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api._common import current_user_id
from app.auth import (
    _DUMMY_HASH,
    auth_configured,
    clear_session_cookie,
    set_session_cookie,
    verify_password,
)
from app.db import run_off_thread


async def login(request: Request) -> JSONResponse:
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password", "")

    conn = request.app.state.get_conn()
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()

    # bcrypt.checkpw is deliberately ~100-300ms of CPU — off the event
    # loop, same pattern as this app's other blocking work (app.db.run_off_thread).
    # It runs even for an unknown username, against a dummy hash, so a bad
    # username and a bad password cost the same wall-clock time.
    ok = await asyncio.to_thread(verify_password, password, row[2] if row else _DUMMY_HASH)
    if row is None or not ok:
        # One message for both failures: which half was wrong is exactly
        # what an attacker wants to learn.
        return JSONResponse({"error": "incorrect username or password"}, status_code=401)

    response = JSONResponse({"ok": True, "id": row[0], "username": row[1]})
    set_session_cookie(response, row[0])
    return response


async def logout(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


def _fetch_user(conn, user_id: int):
    return conn.execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()


async def me(request: Request) -> JSONResponse:
    """Who the current session belongs to. Gated by AuthMiddleware like
    every other /api route, which is the point: its 401 is the frontend's
    single "show the login screen" signal, instead of inferring that from
    whichever data query happened to fail first.

    `auth_enabled` is false on a no-login deployment, where the identity
    below is the default account rather than anyone who signed in — the UI
    uses it to hide a Log out button that would clear a cookie that was
    never gating anything.
    """
    # Off the event loop — one of the 4 calls the frontend fires in
    # parallel on every cold open; see the matching comment on
    # list_articles in api/articles.py.
    row = await run_off_thread(request.app.state.db_path, _fetch_user, current_user_id(request))
    if row is None:
        # A correctly signed cookie for an account that no longer exists.
        response = JSONResponse({"error": "not authenticated"}, status_code=401)
        clear_session_cookie(response)
        return response
    return JSONResponse(
        {"id": row[0], "username": row[1], "auth_enabled": auth_configured()}
    )
