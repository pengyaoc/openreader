"""SQLite-backed job tracking for LLM article generation. Job state lives
in the DB, not in server memory — so a job's outcome survives a server
restart and the UI can poll it from any process.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

_COLUMNS = (
    "id", "topic_key", "status", "brief_snapshot", "model",
    "started_at", "finished_at", "error", "articles_created",
)


def create_job(conn: sqlite3.Connection, topic_key: str, brief: str, model: str) -> int:
    conn.execute(
        "INSERT INTO jobs (topic_key, status, brief_snapshot, model) VALUES (?, 'queued', ?, ?)",
        (topic_key, brief, model),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(zip(_COLUMNS, row))


def mark_running(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()


def mark_done(conn: sqlite3.Connection, job_id: int, articles_created: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', finished_at = ?, articles_created = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), articles_created, job_id),
    )
    conn.commit()


def mark_error(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'error', finished_at = ?, error = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), error, job_id),
    )
    conn.commit()
