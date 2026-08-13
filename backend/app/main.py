"""Starlette app wiring: routes -> handlers, static frontend, app.state
holding the config and a connection factory (fresh sqlite3 connection per
request — WAL mode allows concurrent readers, and this app is single-user
so there is no real concurrency to worry about).
"""
from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import settings
from app.api import articles, config_api, generate_api, images, refresh_api, sources
from app.config import Config, load_config
from app.db import connect, init_schema


def create_app(
    db_path: Path | None = None,
    config: Config | None = None,
    config_path: Path | None = None,
) -> Starlette:
    db_path = db_path or settings.DB_PATH
    config_path = config_path or settings.CONFIG_PATH
    if config is None:
        config = load_config(config_path) if config_path.exists() else Config()

    def get_conn():
        return connect(db_path)

    routes = [
        Route("/api/sources", sources.list_sources),
        Route("/api/sources", sources.add_source, methods=["POST"]),
        Route("/api/sources/{source_id}/mark-all-read", sources.mark_all_read, methods=["POST"]),
        Route("/api/articles", articles.list_articles),
        Route("/api/articles/mark-all-read", articles.mark_all_read, methods=["POST"]),
        Route("/api/articles/{article_id}", articles.get_article),
        Route("/api/articles/{article_id}/read", articles.mark_read, methods=["POST"]),
        Route("/api/articles/{article_id}/star", articles.toggle_star, methods=["POST"]),
        Route("/api/articles/{article_id}/toggle-read", articles.toggle_read, methods=["POST"]),
        Route("/api/refresh", refresh_api.refresh, methods=["POST"]),
        Route("/api/config", config_api.get_config, methods=["GET"]),
        Route("/api/config", config_api.put_config, methods=["PUT"]),
        Route("/api/img", images.proxy_image),
        Route("/api/topics", generate_api.list_topics),
        Route("/api/topics/{topic_key}/generate", generate_api.generate, methods=["POST"]),
        Route("/api/jobs/{job_id}", generate_api.get_job_status),
    ]

    app = Starlette(routes=routes)
    app.state.get_conn = get_conn
    app.state.config = config
    app.state.config_path = config_path
    app.state.db_path = db_path
    return app


def build_production_app() -> Starlette:
    """Entrypoint used by `uvicorn app.main:app`. Ensures the DB schema
    exists and mounts the built frontend if present."""
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.DB_PATH)
    init_schema(conn)
    conn.close()

    app = create_app()

    frontend_dist = settings.REPO_ROOT / "frontend" / "dist"
    if frontend_dist.exists():
        from starlette.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        async def no_frontend(request):
            return JSONResponse(
                {"error": "frontend not built; run `npm run build` in frontend/"},
                status_code=503,
            )

        app.add_route("/", no_frontend)

    return app
