from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api._common import readonly_response
from app.config import ConfigError, parse_config
from app.ingest.refresh import reconcile_read_state


async def get_config(request: Request) -> JSONResponse:
    raw = request.app.state.config_path.read_text()
    return JSONResponse({"yaml": raw})


async def put_config(request: Request) -> JSONResponse:
    # Checked first so a read-only deployment never touches disk — see
    # app.api._common.readonly_response.
    if (resp := readonly_response()) is not None:
        return resp

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
