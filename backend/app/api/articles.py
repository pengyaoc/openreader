import sqlite3

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import store
from app.db import run_off_thread
from app.ingest.hydrate import hydrate_article
from app.ingest.textutil import plain_text_excerpt
from app.summarize import ClaudeError, summarize_text


async def llm_status(request: Request) -> JSONResponse:
    """Whether the reader's one LLM feature (Summarize) is usable —
    read by the frontend to hide the button entirely rather than showing
    one that would just 404 on click."""
    return JSONResponse({"enabled": request.app.state.config.llm.enabled})


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
        source_cfg = config.source(source_key)
        fetch_full_text = (
            source_cfg.fetch_full_text if source_cfg else config.defaults.fetch_full_text
        )
        result = await run_off_thread(
            request.app.state.db_path, hydrate_article, article_id, fetch_full_text
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


def _run_summarize(
    conn: sqlite3.Connection, article_id: int, text: str, word_count: int
) -> tuple[str, str]:
    summary_html = summarize_text(text, word_count)
    llm_summary_at = store.save_summary(conn, article_id, summary_html)
    return summary_html, llm_summary_at


async def summarize(request: Request) -> JSONResponse:
    """Adhoc, on-demand summary of one article's already-fetched text.
    Gated by the llm.enabled kill switch — the UI hides the button and
    this 404s while it's off, so `claude` is never invoked. Cached forever
    once generated: no regenerate path here, since nothing in the feature
    calls for one."""
    config = request.app.state.config
    if not config.llm.enabled:
        return JSONResponse({"error": "LLM generation is disabled"}, status_code=404)

    conn = request.app.state.get_conn()
    article_id = int(request.path_params["article_id"])
    article = store.get_article(conn, article_id)
    if article is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    if article["llm_summary_html"]:
        return JSONResponse(
            {
                "summary_html": article["llm_summary_html"],
                "llm_summary_at": article["llm_summary_at"],
            }
        )

    text = plain_text_excerpt(article["content_html"], limit=None)
    word_count = len(text.split())

    try:
        summary_html, llm_summary_at = await run_off_thread(
            request.app.state.db_path, _run_summarize, article_id, text, word_count
        )
    except ClaudeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)

    return JSONResponse({"summary_html": summary_html, "llm_summary_at": llm_summary_at})


async def mark_all_read(request: Request) -> JSONResponse:
    """Backs the "mark all read" action on the Unread saved view — every
    unread article across every source, not scoped to one like
    sources.mark_all_read. Registered ahead of /api/articles/{article_id}
    in main.py's route table so this literal path wins the match."""
    conn = request.app.state.get_conn()
    marked = store.mark_all_read_global(conn)
    return JSONResponse({"ok": True, "marked": marked})


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
