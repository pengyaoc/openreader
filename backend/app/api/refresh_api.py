from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
from app.connectors import imap as imap_connector
from app.connectors.gmail import access_token_from_token_file
from app.ingest.refresh import refresh_all


async def refresh(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
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
        report = refresh_all(
            conn,
            config.sources,
            only_key=only_key,
            gmail_access_token=gmail_access_token,
            imap_client=imap_client,
        )
    finally:
        if imap_client is not None:
            try:
                imap_client.logout()
            except Exception:  # noqa: BLE001 — best-effort cleanup only
                pass

    return JSONResponse(report)
