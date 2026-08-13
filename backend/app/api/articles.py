import asyncio
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import store
from app.db import connect
from app.ingest.hydrate import hydrate_article


def _hydrate_off_thread(db_path: str | Path, article_id: int, fetch_full_text: bool) -> dict:
    """Runs hydrate_article() on a worker thread via asyncio.to_thread, with
    its own SQLite connection rather than the request's — sqlite3
    connections default to check_same_thread=True, so the request's
    connection (opened on the event loop thread) can't cross into a worker
    thread. A fresh connection here is cheap and matches the app's existing
    "fresh connection per use" pattern (see app.state.get_conn).

    The reason this needs a thread at all: hydrate_article's fetcher does a
    synchronous httpx.get with up to a 5s timeout. Called directly from an
    async handler, that blocks uvicorn's single event loop for the fetch's
    full duration — not just for the one request, but for every other
    request the process is handling (other articles, refresh, image
    proxying) until it returns. First-open of any fetch_full_text source's
    article was the concrete symptom reported (2026-08-13)."""
    conn = connect(db_path)
    try:
        return hydrate_article(conn, article_id, fetch_full_text=fetch_full_text)
    finally:
        conn.close()


async def list_articles(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    view = request.query_params.get("view", "all")
    source_id = request.query_params.get("source_id")
    folder = request.query_params.get("folder")
    limit = int(request.query_params.get("limit", "50"))
    offset = int(request.query_params.get("offset", "0"))

    articles = store.list_articles(
        conn,
        view=view,
        source_id=int(source_id) if source_id else None,
        folder=folder,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(articles)


async def get_article(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    article_id = int(request.path_params["article_id"])
    article = store.get_article(conn, article_id)
    if article is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    if article["origin"] == "feed":
        row = conn.execute(
            "SELECT key FROM sources WHERE id = ?", (article["source_id"],)
        ).fetchone()
        source_key = row[0] if row else None
        config = request.app.state.config
        source_cfg = next((s for s in config.sources if s.key == source_key), None)
        fetch_full_text = (
            source_cfg.fetch_full_text if source_cfg else config.defaults.fetch_full_text
        )
        result = await asyncio.to_thread(
            _hydrate_off_thread, request.app.state.db_path, article_id, fetch_full_text
        )
        if result["content_html"]:
            article["content_html"] = result["content_html"]
        # hydrate_article may have just written hydrated_at/hydrate_failed_at;
        # re-read those two fields so the response reflects DB truth.
        row = conn.execute(
            "SELECT hydrated_at, hydrate_failed_at FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        article["hydrated_at"], article["hydrate_failed_at"] = row

    return JSONResponse(article)


async def mark_read(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    article_id = int(request.path_params["article_id"])
    ok = store.mark_read(conn, article_id)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def toggle_star(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    article_id = int(request.path_params["article_id"])
    result = store.toggle_star(conn, article_id)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


async def toggle_read(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    article_id = int(request.path_params["article_id"])
    result = store.toggle_read(conn, article_id)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)
