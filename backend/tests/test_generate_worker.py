"""Tests for the generation worker's orchestration: topic lookup, virtual
source creation, article persistence with provenance, and job status
transitions. generate_fn is injected — no real subprocess in these tests.
"""
import json

from app.config import Config, LLMSettings, Topic
from app.db import connect, init_schema
from app.generate.client import ClaudeError
from app.generate.jobs import create_job, get_job
from app.generate.worker import run_job


def make_config(topics=None, timeout_minutes=10):
    return Config(
        llm=LLMSettings(enabled=True, model="sonnet", timeout_minutes=timeout_minutes),
        topics=topics or [],
    )


def test_run_job_persists_generated_articles_with_provenance(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    topic = Topic(key="ai-evals", title="AI Evals", folder="Generated", brief="track AI eval news")
    config = make_config(topics=[topic])
    job_id = create_job(conn, topic_key="ai-evals", brief=topic.brief, model="sonnet")

    def fake_generate(prompt, model, timeout):
        return [
            {
                "title": "New eval benchmark released",
                "summary": "A summary.",
                "body_html": "<p>Full body text about the new benchmark.</p>",
                "sources": [{"title": "Paper", "url": "https://arxiv.org/x"}],
            }
        ]

    run_job(conn, job_id, config, generate_fn=fake_generate)

    job = get_job(conn, job_id)
    assert job["status"] == "done"
    assert job["articles_created"] == 1

    row = conn.execute(
        "SELECT title, origin, job_id, citations_json, excerpt FROM articles"
    ).fetchone()
    assert row[0] == "New eval benchmark released"
    assert row[1] == "llm"
    assert row[2] == job_id
    citations = json.loads(row[3])
    assert citations[0]["url"] == "https://arxiv.org/x"
    assert "Full body text" in row[4]


def test_run_job_creates_a_virtual_source_matching_the_topic(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    topic = Topic(key="ai-evals", title="AI Evals", folder="Generated", brief="b")
    config = make_config(topics=[topic])
    job_id = create_job(conn, topic_key="ai-evals", brief="b", model="sonnet")

    run_job(conn, job_id, config, generate_fn=lambda p, model, timeout: [])

    source = conn.execute(
        "SELECT key, type, title, folder FROM sources WHERE key = 'ai-evals'"
    ).fetchone()
    assert source == ("ai-evals", "llm", "AI Evals", "Generated")


def test_run_job_reuses_the_same_virtual_source_across_multiple_jobs(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    topic = Topic(key="ai-evals", title="AI Evals", folder="Generated", brief="b")
    config = make_config(topics=[topic])

    job1 = create_job(conn, topic_key="ai-evals", brief="b", model="sonnet")
    run_job(conn, job1, config, generate_fn=lambda p, model, timeout: [])
    job2 = create_job(conn, topic_key="ai-evals", brief="b", model="sonnet")
    run_job(conn, job2, config, generate_fn=lambda p, model, timeout: [])

    count = conn.execute("SELECT COUNT(*) FROM sources WHERE key = 'ai-evals'").fetchone()[0]
    assert count == 1


def test_run_job_writes_zero_articles_on_claude_error(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    topic = Topic(key="ai-evals", title="AI Evals", folder="Generated", brief="b")
    config = make_config(topics=[topic])
    job_id = create_job(conn, topic_key="ai-evals", brief="b", model="sonnet")

    def failing_generate(prompt, model, timeout):
        raise ClaudeError("generation timed out after 600.0s")

    run_job(conn, job_id, config, generate_fn=failing_generate)

    job = get_job(conn, job_id)
    assert job["status"] == "error"
    assert "timed out" in job["error"]
    count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert count == 0


def test_run_job_errors_when_topic_no_longer_in_config(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    config = make_config(topics=[])  # topic removed from config since job was queued
    job_id = create_job(conn, topic_key="ghost-topic", brief="b", model="sonnet")

    run_job(conn, job_id, config, generate_fn=lambda p, model, timeout: [])

    job = get_job(conn, job_id)
    assert job["status"] == "error"
    assert "ghost-topic" in job["error"]


def test_run_job_handles_zero_articles_as_success_not_error(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    topic = Topic(key="ai-evals", title="AI Evals", folder="Generated", brief="b")
    config = make_config(topics=[topic])
    job_id = create_job(conn, topic_key="ai-evals", brief="b", model="sonnet")

    run_job(conn, job_id, config, generate_fn=lambda p, model, timeout: [])

    job = get_job(conn, job_id)
    assert job["status"] == "done"
    assert job["articles_created"] == 0


def test_run_job_is_a_noop_for_a_missing_job_id(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    config = make_config(topics=[])
    run_job(conn, 9999, config, generate_fn=lambda p, model, timeout: [])  # must not raise
