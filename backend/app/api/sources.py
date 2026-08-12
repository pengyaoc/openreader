import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import store
from app.config import ConfigError, Rule, Source, to_yaml, validate_config
from app.ingest.refresh import get_or_create_source


async def list_sources(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    return JSONResponse(store.list_sources(conn))


async def add_source(request: Request) -> JSONResponse:
    """Structured source creation for the 'Add source' UI — the alternative
    to hand-editing config/feeds.yaml. Validates exactly like a manual edit
    (bad regex, duplicate key, missing required field all reject the same
    way) and writes back through the same to_yaml() serializer."""
    body = await request.json()

    try:
        rules = [msgspec.convert(r, type=Rule, strict=False) for r in body.get("rules", [])]
        source = msgspec.convert({**body, "rules": rules}, type=Source, strict=False)
    except msgspec.ValidationError as exc:
        return JSONResponse({"error": f"invalid source: {exc}"}, status_code=400)

    config = request.app.state.config
    new_config = msgspec.structs.replace(config, sources=[*config.sources, source])
    try:
        validate_config(new_config)
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    request.app.state.config_path.write_text(to_yaml(new_config))
    request.app.state.config = new_config

    # Make it appear in the sidebar immediately — GET /api/sources reads the
    # DB, and without this the source would be invisible until the next
    # refresh happens to create its row as a side effect.
    conn = request.app.state.get_conn()
    get_or_create_source(conn, source)

    return JSONResponse({"ok": True, "key": source.key}, status_code=201)
