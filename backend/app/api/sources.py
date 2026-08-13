import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import store
from app.api._common import readonly_response, save_config
from app.config import ConfigError, Rule, Source, validate_config
from app.ingest.refresh import get_or_create_source, reconcile_read_state


async def list_sources(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    valid_keys = {s.key for s in request.app.state.config.sources}
    return JSONResponse(store.list_sources(conn, valid_keys=valid_keys))


async def add_source(request: Request) -> JSONResponse:
    """Structured source creation for the 'Add source' UI — the alternative
    to hand-editing config/feeds.yaml. Validates exactly like a manual edit
    (bad regex, duplicate key, missing required field all reject the same
    way) and writes back through the same to_yaml() serializer. Works for
    any SourceType (rss/imap) the request body specifies — nothing
    here is RSS-specific; that restriction was only ever in the old
    frontend modal."""
    if (resp := readonly_response()) is not None:
        return resp

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

    save_config(request, new_config)

    # Make it appear in the sidebar immediately — GET /api/sources reads the
    # DB, and without this the source would be invisible until the next
    # refresh happens to create its row as a side effect.
    conn = request.app.state.get_conn()
    get_or_create_source(conn, source)

    return JSONResponse({"ok": True, "key": source.key}, status_code=201)


async def get_source(request: Request) -> JSONResponse:
    """Full detail for one source: list_sources' DB-derived fields merged
    with config-derived fields (url/query/mailbox_folder/fetch_full_text/
    rules) that the lean sidebar listing doesn't include. Backs the edit
    modal's pre-fill — editing needs the actual feed URL or IMAP query,
    not just what the sidebar shows."""
    conn = request.app.state.get_conn()
    source_id = int(request.path_params["source_id"])
    row = store.get_source(conn, source_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    config = request.app.state.config
    cfg_source = config.source(row["key"])
    if cfg_source is None:
        return JSONResponse({"error": "source not in current config"}, status_code=404)

    return JSONResponse(
        {
            **row,
            "url": cfg_source.url,
            "query": cfg_source.query,
            "mailbox_folder": cfg_source.mailbox_folder,
            "fetch_full_text": cfg_source.fetch_full_text,
            "rules": [
                {"action": r.action, "field": r.field, "pattern": r.pattern}
                for r in cfg_source.rules
            ],
        }
    )


async def update_source(request: Request) -> JSONResponse:
    """Edits an existing source's fields in place — title, folder,
    url/query/mailbox_folder, fetch_full_text, rules. `key` and `type` are
    identity, not editable here: they're locked to the existing entry's
    values regardless of what the request body sends, because changing
    either is really "delete this, add a different one" — dedup
    (source_id, guid) and the sidebar/history all key off a stable key.

    Also updates the sources DB row's title/folder/url directly — unlike
    a refresh, which never revisits an existing row's stored title/folder
    once created (get_or_create_source only inserts, never updates), so
    without this an edit would silently not show up in the sidebar until
    someone reads the code closely enough to notice it never will.
    """
    if (resp := readonly_response()) is not None:
        return resp

    conn = request.app.state.get_conn()
    source_id = int(request.path_params["source_id"])
    row = conn.execute("SELECT key FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    key = row[0]

    config = request.app.state.config
    existing = config.source(key)
    if existing is None:
        return JSONResponse({"error": "source not in current config"}, status_code=404)

    body = await request.json()
    body = {**body, "key": existing.key, "type": existing.type}

    try:
        rules = [msgspec.convert(r, type=Rule, strict=False) for r in body.get("rules", [])]
        updated = msgspec.convert({**body, "rules": rules}, type=Source, strict=False)
    except msgspec.ValidationError as exc:
        return JSONResponse({"error": f"invalid source: {exc}"}, status_code=400)

    new_sources = [updated if s.key == key else s for s in config.sources]
    new_config = msgspec.structs.replace(config, sources=new_sources)
    try:
        validate_config(new_config)
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    save_config(request, new_config)

    conn.execute(
        "UPDATE sources SET title = ?, folder = ?, url = ? WHERE id = ?",
        (updated.title, updated.folder, updated.url, source_id),
    )
    conn.commit()

    # Rules may have changed — sweep articles that no longer pass, same as
    # a raw-YAML config save does.
    reconciled = reconcile_read_state(conn, new_config)

    return JSONResponse({"ok": True, "key": updated.key, "reconciled": reconciled})


async def remove_source(request: Request) -> JSONResponse:
    """Removes a source from config only — its DB row and articles are
    never deleted (matches PUT /api/config's existing behavior for a
    source dropped via a raw YAML edit). The sidebar filter
    (list_sources' valid_keys) hides it immediately; reconcile_read_state
    marks its still-unread articles read, same as any other config-driven
    removal — see that function's docstring for the full reasoning."""
    if (resp := readonly_response()) is not None:
        return resp

    conn = request.app.state.get_conn()
    source_id = int(request.path_params["source_id"])
    row = conn.execute("SELECT key FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    key = row[0]

    config = request.app.state.config
    new_sources = [s for s in config.sources if s.key != key]
    if len(new_sources) == len(config.sources):
        return JSONResponse({"error": "source not in current config"}, status_code=404)
    new_config = msgspec.structs.replace(config, sources=new_sources)

    save_config(request, new_config)

    reconciled = reconcile_read_state(conn, new_config)

    return JSONResponse({"ok": True, "reconciled": reconciled})


async def mark_all_read(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    source_id = int(request.path_params["source_id"])
    marked = store.mark_all_read(conn, source_id)
    if marked is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "marked": marked})
