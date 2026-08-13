import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
from app.connectors import imap as imap_connector
from app.connectors.gmail import access_token_from_token_file
from app.db import connect
from app.ingest.refresh import refresh_all


def _refresh_off_thread(
    db_path: str | Path,
    sources,
    only_key: str | None,
    gmail_access_token: str | None,
    imap_client,
) -> dict:
    """Runs the whole refresh on a worker thread via asyncio.to_thread, with
    its own SQLite connection — sqlite3 connections default to
    check_same_thread=True, and the request's connection belongs to the
    event loop thread (same pattern as articles.py's _hydrate_off_thread).

    Without this, refresh — even with the RSS batch now fetching
    concurrently (refresh.py) — still blocks uvicorn's single event loop
    for its entire duration: measured live, 2026-08-13, a request sent
    0.3s into a refresh wasn't served until the refresh finished ~8.4s
    later. Nothing else in the app (article reads, image loads, the
    sidebar) can be served while a refresh via the old code path was
    running.
    """
    conn = connect(db_path)
    try:
        return refresh_all(
            conn,
            sources,
            only_key=only_key,
            gmail_access_token=gmail_access_token,
            imap_client=imap_client,
        )
    finally:
        conn.close()


async def refresh(request: Request) -> JSONResponse:
    config = request.app.state.config
    only_key = request.query_params.get("source")

    gmail_access_token = None
    if any(s.type == "gmail" for s in config.sources):
        try:
            gmail_access_token = access_token_from_token_file(settings.TOKEN_PATH)
        except Exception:  # noqa: BLE001 — a broken/expired token shouldn't crash refresh
            gmail_access_token = None

    imap_client = None
    imap_configured = settings.IMAP_HOST and settings.IMAP_USER and settings.IMAP_PASSWORD
    if any(s.type == "imap" for s in config.sources) and imap_configured:
        try:
            imap_client = imap_connector.connect(
                settings.IMAP_HOST, settings.IMAP_USER, settings.IMAP_PASSWORD
            )
        except imap_connector.ImapError:
            # refresh_all reports "IMAP not configured" for imap_client=None,
            # which is a slightly inaccurate message for "configured but the
            # login failed" — acceptable for v1; the per-source error isn't
            # otherwise surfaced anywhere a broken login couldn't also break
            # a broken config.
            imap_client = None

    try:
        report = await asyncio.to_thread(
            _refresh_off_thread,
            request.app.state.db_path,
            config.sources,
            only_key,
            gmail_access_token,
            imap_client,
        )
    finally:
        if imap_client is not None:
            try:
                imap_client.logout()
            except Exception:  # noqa: BLE001 — best-effort cleanup only
                pass

    return JSONResponse(report)
