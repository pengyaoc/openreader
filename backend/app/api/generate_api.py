"""LLM article generation endpoints. Manual-trigger only — no scheduler
anywhere in this module. llm.enabled=false is a hard kill switch: both
endpoints 404 and spawn_worker is never called.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import msgspec
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.generate.jobs import create_job, get_job

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _default_spawn(job_id: int) -> None:
    subprocess.Popen([sys.executable, "-m", "app.generate.worker", str(job_id)], cwd=BACKEND_DIR)


# Module-level indirection so tests can monkeypatch this without touching a
# real subprocess — same pattern as the injectable Fetcher/Runner elsewhere.
spawn_worker = _default_spawn


async def list_topics(request: Request) -> JSONResponse:
    config = request.app.state.config
    return JSONResponse(
        {
            "enabled": config.llm.enabled,
            "topics": [msgspec.to_builtins(t) for t in config.topics],
        }
    )


async def generate(request: Request) -> JSONResponse:
    config = request.app.state.config
    if not config.llm.enabled:
        return JSONResponse({"error": "LLM generation is disabled"}, status_code=404)

    topic_key = request.path_params["topic_key"]
    topic = next((t for t in config.topics if t.key == topic_key), None)
    if topic is None:
        return JSONResponse({"error": f"unknown topic: {topic_key}"}, status_code=404)

    conn = request.app.state.get_conn()
    job_id = create_job(conn, topic_key=topic.key, brief=topic.brief, model=config.llm.model)
    spawn_worker(job_id)
    return JSONResponse({"job_id": job_id}, status_code=202)


async def get_job_status(request: Request) -> JSONResponse:
    conn = request.app.state.get_conn()
    job_id = int(request.path_params["job_id"])
    job = get_job(conn, job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(job)
