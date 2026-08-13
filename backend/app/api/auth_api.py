import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth import clear_session_cookie, set_session_cookie, verify_password


async def login(request: Request) -> JSONResponse:
    body = await request.json()
    password = body.get("password", "")

    # bcrypt.checkpw is deliberately ~100-300ms of CPU — off the event
    # loop, same pattern as this app's other blocking work (app.db.run_off_thread).
    ok = await asyncio.to_thread(verify_password, password)
    if not ok:
        return JSONResponse({"error": "incorrect password"}, status_code=401)

    response = JSONResponse({"ok": True})
    set_session_cookie(response)
    return response


async def logout(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response
