import asyncio
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
from app.api._common import AnonymousUserError, require_user_id
from app.connectors import imap as imap_connector
from app.db import run_off_thread
from app.ingest.hydrate import hydrate_in_background
from app.ingest.refresh import hydration_scope, refresh_all


async def refresh(request: Request) -> JSONResponse:
    try:
        require_user_id(request)
    except AnonymousUserError:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    report = await run_refresh(request.app, request.query_params.get("source"))
    return JSONResponse(report)


async def run_refresh(app, only_key: str | None = None, *, wait: bool = True) -> dict | None:
    """One refresh, shared by the Refresh button and the auto-refresh timer
    (app/autorefresh.py). Refreshes run one at a time under
    app.state.refresh_lock: with wait=True (the button) this waits its
    turn; with wait=False (the timer) it returns None at once if a refresh
    is already running. A full refresh (no only_key) records its start time
    in app.state.last_full_refresh, which the timer counts from."""
    lock: asyncio.Lock = app.state.refresh_lock
    if not wait and lock.locked():
        return None
    async with lock:
        started = datetime.now(UTC)
        report = await _refresh(app, only_key)
        if only_key is None:
            app.state.last_full_refresh = started
        return report


async def _refresh(app, only_key: str | None) -> dict:
    config = app.state.config
    # A factory, not a pre-opened connection: refresh.py's
    # _refresh_imap_sequential calls this once and reuses the one
    # connection across every IMAP source in the refresh (2026-08-13:
    # per-source concurrent connections made every refresh open as many
    # fresh Gmail logins as there were IMAP sources, from one datacenter
    # IP — Gmail's abuse heuristics answered that by silently stalling the
    # connection instead of erroring). Passed as a factory rather than a
    # pre-opened client so a failed connect reports its own real error
    # (bad password, network blip, etc.) instead of a blanket "not
    # configured".
    imap_connect = None
    imap_configured = settings.IMAP_HOST and settings.IMAP_USER and settings.IMAP_PASSWORD
    if any(s.type == "imap" for s in config.sources) and imap_configured:
        imap_connect = lambda: imap_connector.connect(
            settings.IMAP_HOST, settings.IMAP_USER, settings.IMAP_PASSWORD
        )

    # Off the event loop thread (see app.db.run_off_thread) — without this,
    # refresh blocks uvicorn's single event loop for its entire duration:
    # measured live 2026-08-13, a request sent 0.3s into a refresh wasn't
    # served until the refresh finished ~8.4s later. Nothing else in the
    # app (article reads, image loads, the sidebar) can be served while a
    # refresh via the old code path was running.
    report = await run_off_thread(
        app.state.db_path,
        refresh_all,
        config.sources,
        only_key=only_key,
        imap_connect=imap_connect,
        hydrate=False,
    )

    # Full-text hydration used to be the tail of refresh_all, and it was most
    # of a refresh's wall time (benchmark, 2026-10-17: 4.7s of 5.2s). New
    # articles are already in the DB at this point, so answer now and let
    # the hydration finish on its own thread. Same scope as before: only the
    # sources this refresh covered.
    refreshed = [s for s in config.sources if only_key is None or s.key == only_key]
    hydrate_in_background(app.state.db_path, hydration_scope(refreshed))

    return report
