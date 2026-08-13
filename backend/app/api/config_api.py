from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
from app.config import ConfigError, parse_config
from app.ingest.refresh import reconcile_read_state


async def get_config(request: Request) -> JSONResponse:
    raw = request.app.state.config_path.read_text()
    return JSONResponse({"yaml": raw})


async def put_config(request: Request) -> JSONResponse:
    # Hard write-lock, set via READER_READONLY_CONFIG on deployments where
    # this endpoint is reachable by more than just its one trusted user
    # (unauthenticated by design elsewhere in v1) — edit feeds.yaml via SSH
    # instead. Checked first so a read-only deployment never touches disk.
    if settings.READONLY_CONFIG:
        return JSONResponse(
            {"error": "config is read-only on this deployment"}, status_code=403
        )

    body = await request.json()
    raw_yaml = body.get("yaml", "")
    try:
        config = parse_config(raw_yaml)
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    request.app.state.config_path.write_text(raw_yaml)
    request.app.state.config = config

    conn = request.app.state.get_conn()
    reconciled = reconcile_read_state(conn, config)

    return JSONResponse({"ok": True, "reconciled": reconciled})
