"""Starlette app wiring: routes -> handlers, static frontend, app.state
holding the config and a connection factory (fresh sqlite3 connection per
request — WAL mode allows concurrent readers, and with a household's worth
of accounts there is no real write contention to worry about).
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from app import autorefresh, settings
from app.api import (
    articles,
    auth_api,
    config_api,
    images,
    refresh_api,
    sources,
    webfeed,
)
from app.auth import auth_configured, load_auth_config, upsert_user
from app.config import Config, load_config
from app.db import connect, init_schema
from app.imgcache import ImageCache
from app.ingest.refresh import sync_sources
from app.pchauth.starlette_adapter import PchauthMiddleware
from app.timing import TimingMiddleware


class FrontendStaticFiles(StaticFiles):
    """Vite fingerprints everything under assets/ (index-<hash>.js), so those
    files can be cached forever. Without a Cache-Control header the browser
    sent a revalidation request for each one on every cold open — a round
    trip per file before the app could even start. Everything else,
    index.html above all, is revalidated each time so a deploy shows up on
    the next load."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        if Path(full_path).parent.name == "assets":
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    db_path: Path | None = None,
    config: Config | None = None,
    config_path: Path | None = None,
    require_auth: bool = True,
    auto_refresh: bool = False,
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
        Route("/api/sources/{source_id}", sources.get_source),
        Route("/api/sources/{source_id}", sources.update_source, methods=["PUT"]),
        Route("/api/sources/{source_id}", sources.remove_source, methods=["DELETE"]),
        Route("/api/sources/{source_id}/mark-all-read", sources.mark_all_read, methods=["POST"]),
        Route("/api/articles", articles.list_articles),
        Route("/api/articles/mark-all-read", articles.mark_all_read, methods=["POST"]),
        Route("/api/articles/{article_id}", articles.get_article),
        Route("/api/articles/{article_id}/read", articles.mark_read, methods=["POST"]),
        Route("/api/articles/{article_id}/star", articles.toggle_star, methods=["POST"]),
        Route("/api/articles/{article_id}/toggle-read", articles.toggle_read, methods=["POST"]),
        Route("/api/articles/{article_id}/summarize", articles.summarize, methods=["POST"]),
        Route("/api/articles/{article_id}/chat", articles.chat, methods=["GET", "POST", "DELETE"]),
        Route("/api/llm-status", articles.llm_status),
        Route("/api/refresh", refresh_api.refresh, methods=["POST"]),
        Route("/api/config", config_api.get_config, methods=["GET"]),
        Route("/api/config", config_api.put_config, methods=["PUT"]),
        Route("/api/img", images.proxy_image),
        Route("/api/webfeed/preview", webfeed.preview, methods=["POST"]),
        Route("/api/me", auth_api.me),
    ]

    # TimingMiddleware wraps outermost so its clock covers auth too — see
    # app/timing.py for why (chasing an intermittent cold-open slowdown).
    # GZip next: Apache compresses the static frontend but not
    # application/json, so a 50-row article page went out as ~88KB instead
    # of ~12KB (measured 2026-10-17). Level 6 rather than Starlette's 9: on
    # the shared 1-vCPU VM, 9 costs noticeably more CPU for ~1% smaller
    # output. Images are already compressed and in Starlette's exclude list.
    middleware = [Middleware(TimingMiddleware), Middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)]
    if require_auth:
        user_ids: dict[str, int] = {}
        middleware.append(
            Middleware(
                PchauthMiddleware,
                config=load_auth_config(),
                upsert_user=lambda identity: upsert_user(identity, get_conn, user_ids),
            )
        )
    @contextlib.asynccontextmanager
    async def lifespan(app):
        # Production only (build_production_app): tests leave it off so no
        # timer runs behind them. The clock starts from the newest fetch in
        # the DB, so a restart doesn't cost an extra refresh.
        conn = get_conn()
        try:
            app.state.last_full_refresh = autorefresh.latest_fetch(conn)
        finally:
            conn.close()
        task = asyncio.create_task(autorefresh.auto_refresh_loop(app))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan if auto_refresh else None)
    app.state.get_conn = get_conn
    app.state.config = config
    app.state.config_path = config_path
    app.state.db_path = db_path
    # One refresh at a time, button or timer (app/autorefresh.py).
    app.state.refresh_lock = asyncio.Lock()
    app.state.last_full_refresh = None
    # Fixed cwd for every chat `claude` call: the CLI files sessions under
    # ~/.claude/projects/<encoded cwd>/, and --resume only looks there.
    app.state.chat_cwd = Path(db_path).parent / "claude-chat"
    app.state.chat_cwd.mkdir(parents=True, exist_ok=True)
    image_cache_dir = Path(settings.IMAGE_CACHE_DIR) if settings.IMAGE_CACHE_DIR else Path(db_path).parent / "imgcache"
    app.state.image_cache = ImageCache(image_cache_dir, settings.IMAGE_CACHE_MAX_MB * 1024 * 1024)
    return app


def build_production_app() -> Starlette:
    """Entrypoint used by `uvicorn app.main:app`. Ensures the DB schema
    exists and mounts the built frontend if present."""
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.DB_PATH)
    init_schema(conn)
    accounts = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()

    # A DB with real accounts in it, served with READER_AUTH_MODE unset
    # (== 'off'), is serving everyone's reading as one unauthenticated
    # account. Warn rather than refuse to boot — running a copy of the
    # production DB locally with no mode set is a routine, legitimate
    # thing to do.
    if accounts > 1 and not auth_configured():
        print(
            f"WARNING: {accounts} accounts exist but READER_AUTH_MODE is unset (or 'off') — "
            "no login is required and every request is served as the first account.",
            file=sys.stderr,
            flush=True,
        )

    # trusted_header mode is safe only because Apache is the sole thing
    # that can reach this process — the identity header is trusted with no
    # verification of its own. uvicorn's bind address is a CLI flag
    # (--uds/--host), invisible to this module, so this can't be a hard
    # runtime check; it's a loud reminder instead. The deployed systemd
    # unit listens on a Unix socket only Apache can open (see README.md's
    # ExecStart, and docs/WORKLOG.md 2026-11-08) — never move it to a TCP
    # port, least of all 0.0.0.0, while auth_configured() is true.
    if auth_configured():
        print(
            "NOTE: READER_AUTH_MODE requires a login — this process must be "
            "reachable ONLY via the trusted Apache gateway (a Unix "
            "socket only Apache can open; never 0.0.0.0). The identity header is trusted with "
            "no verification of its own.",
            file=sys.stderr,
            flush=True,
        )

    app = create_app(auto_refresh=True)

    # Sources hand-added to feeds.yaml get their DB row (and so their
    # sidebar entry) now, not on their first refresh.
    conn = connect(settings.DB_PATH)
    try:
        sync_sources(conn, app.state.config)
    finally:
        conn.close()

    frontend_dist = settings.REPO_ROOT / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", FrontendStaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        async def no_frontend(request):
            return JSONResponse(
                {"error": "frontend not built; run `npm run build` in frontend/"},
                status_code=503,
            )

        app.add_route("/", no_frontend)

    return app
