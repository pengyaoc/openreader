from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import ConfigError, parse_config


async def get_config(request: Request) -> JSONResponse:
    raw = request.app.state.config_path.read_text()
    return JSONResponse({"yaml": raw})


async def put_config(request: Request) -> JSONResponse:
    body = await request.json()
    raw_yaml = body.get("yaml", "")
    try:
        config = parse_config(raw_yaml)
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    request.app.state.config_path.write_text(raw_yaml)
    request.app.state.config = config
    return JSONResponse({"ok": True})
