"""Orchestrates one generation job: look up the topic, run the model, and
persist whatever it returns as normal `articles` rows with hard provenance
(origin='llm', job_id, citations_json). Meant to run out-of-process — see
__main__ below — so the model subprocess's memory never touches the server.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.config import Config, Topic
from app.generate.client import ClaudeError, Runner, generate_articles
from app.generate.jobs import get_job, mark_done, mark_error, mark_running
from app.generate.prompt import build_user_prompt
from app.ingest.textutil import plain_text_excerpt, proxy_image_urls

EXCERPT_LIMIT = 900

GenerateFn = Runner  # (prompt, model, timeout) -> list[dict], swappable in tests


def get_or_create_llm_source(conn: sqlite3.Connection, topic: Topic) -> int:
    row = conn.execute("SELECT id FROM sources WHERE key = ?", (topic.key,)).fetchone()
    if row:
        return row[0]
    conn.execute(
        "INSERT INTO sources (key, type, title, folder) VALUES (?, 'llm', ?, ?)",
        (topic.key, topic.title, topic.folder),
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE key = ?", (topic.key,)).fetchone()[0]


def persist_generated_article(
    conn: sqlite3.Connection, source_id: int, job_id: int, index: int, article: dict
) -> None:
    body_html = proxy_image_urls(article.get("body_html", ""))
    excerpt = plain_text_excerpt(article.get("body_html", ""), limit=EXCERPT_LIMIT) or article.get(
        "summary", ""
    )
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """INSERT INTO articles
           (source_id, guid, url, title, published_at, fetched_at,
            excerpt, content_html, origin, job_id, citations_json)
           VALUES (?, ?, '', ?, ?, ?, ?, ?, 'llm', ?, ?)""",
        (
            source_id,
            f"job-{job_id}-{index}",
            article.get("title", ""),
            now,
            now,
            excerpt,
            body_html,
            job_id,
            json.dumps(article.get("sources", [])),
        ),
    )
    conn.commit()


def run_job(
    conn: sqlite3.Connection,
    job_id: int,
    config: Config,
    generate_fn: GenerateFn = generate_articles,
) -> None:
    job = get_job(conn, job_id)
    if job is None:
        return

    topic = next((t for t in config.topics if t.key == job["topic_key"]), None)
    if topic is None:
        mark_error(conn, job_id, f"topic '{job['topic_key']}' no longer exists in config")
        return

    mark_running(conn, job_id)
    source_id = get_or_create_llm_source(conn, topic)
    prompt = build_user_prompt(topic.brief, topic.lookback_days, topic.max_articles)
    timeout = config.llm.timeout_minutes * 60

    try:
        articles = generate_fn(prompt, model=job["model"], timeout=timeout)
    except ClaudeError as exc:
        mark_error(conn, job_id, str(exc))
        return

    for index, article in enumerate(articles):
        persist_generated_article(conn, source_id, job_id, index, article)

    mark_done(conn, job_id, len(articles))


def main() -> None:
    import sys

    from app import settings
    from app.config import load_config
    from app.db import connect

    job_id = int(sys.argv[1])
    config = load_config(settings.CONFIG_PATH)
    conn = connect(settings.DB_PATH)
    run_job(conn, job_id, config)


if __name__ == "__main__":
    main()
