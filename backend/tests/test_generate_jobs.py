"""Tests for LLM generation job tracking (SQLite-backed)."""
from app.db import connect, init_schema
from app.generate.jobs import create_job, get_job, mark_done, mark_error, mark_running


def test_create_job_starts_queued(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)

    job_id = create_job(conn, topic_key="ai-evals", brief="track AI eval news", model="sonnet")

    job = get_job(conn, job_id)
    assert job["status"] == "queued"
    assert job["topic_key"] == "ai-evals"
    assert job["brief_snapshot"] == "track AI eval news"
    assert job["model"] == "sonnet"
    assert job["articles_created"] == 0


def test_mark_running_updates_status_and_started_at(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    job_id = create_job(conn, topic_key="t", brief="b", model="sonnet")

    mark_running(conn, job_id)

    job = get_job(conn, job_id)
    assert job["status"] == "running"
    assert job["started_at"] is not None


def test_mark_done_sets_status_and_article_count(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    job_id = create_job(conn, topic_key="t", brief="b", model="sonnet")
    mark_running(conn, job_id)

    mark_done(conn, job_id, articles_created=3)

    job = get_job(conn, job_id)
    assert job["status"] == "done"
    assert job["articles_created"] == 3
    assert job["finished_at"] is not None


def test_mark_error_sets_status_and_message(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    job_id = create_job(conn, topic_key="t", brief="b", model="sonnet")
    mark_running(conn, job_id)

    mark_error(conn, job_id, "generation timed out after 600.0s")

    job = get_job(conn, job_id)
    assert job["status"] == "error"
    assert job["error"] == "generation timed out after 600.0s"
    assert job["finished_at"] is not None


def test_get_job_returns_none_for_missing_id(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    assert get_job(conn, 9999) is None
