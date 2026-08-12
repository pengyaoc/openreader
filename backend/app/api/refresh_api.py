from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
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

    report = refresh_all(
        conn, config.sources, only_key=only_key, gmail_access_token=gmail_access_token
    )
    return JSONResponse(report)
