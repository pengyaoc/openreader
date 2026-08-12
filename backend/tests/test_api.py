"""API-level tests using Starlette's TestClient against an in-memory-backed
app instance (temp DB + temp config per test).
"""
import pytest
from starlette.testclient import TestClient

from app.config import Config, Source
from app.db import connect, init_schema
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES (?, ?, ?, ?, ?)",
        ("s1", "rss", "Source One", "Test", "https://x/feed"),
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    conn.execute(
        """INSERT INTO articles
           (source_id, guid, url, title, excerpt, content_html, published_at, origin)
           VALUES (?, 'g1', 'https://x/1', 'Hello World', 'An excerpt', '<p>Body</p>',
                   '2026-08-01T00:00:00Z', 'feed')""",
        (source_id,),
    )
    conn.commit()
    conn.close()

    config = Config(sources=[Source(key="s1", type="rss", title="Source One", folder="Test", url="https://x/feed")])
    # config_path MUST be a tmp path — any endpoint that writes config
    # (PUT /api/config, POST /api/sources) writes here, and without an
    # explicit override it defaults to the real project config/feeds.yaml.
    config_path = tmp_path / "feeds.yaml"
    from app.config import to_yaml

    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path)
    return TestClient(app)


def test_list_sources_includes_unread_count(client):
    resp = client.get("/api/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["key"] == "s1"
    assert data[0]["unread_count"] == 1


def test_list_articles_returns_the_seeded_article(client):
    resp = client.get("/api/articles")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Hello World"
    assert data[0]["is_read"] is False


def test_get_article_detail(client):
    resp = client.get("/api/articles")
    article_id = resp.json()[0]["id"]

    resp = client.get(f"/api/articles/{article_id}")
    assert resp.status_code == 200
    assert resp.json()["content_html"] == "<p>Body</p>"


def test_get_article_404_for_missing_id(client):
    resp = client.get("/api/articles/9999")
    assert resp.status_code == 404


def test_marking_article_read_updates_state_and_unread_count(client):
    article_id = client.get("/api/articles").json()[0]["id"]

    resp = client.post(f"/api/articles/{article_id}/read")
    assert resp.status_code == 200

    detail = client.get(f"/api/articles/{article_id}").json()
    assert detail["is_read"] is True

    sources = client.get("/api/sources").json()
    assert sources[0]["unread_count"] == 0


def test_unread_view_excludes_read_articles(client):
    article_id = client.get("/api/articles").json()[0]["id"]
    client.post(f"/api/articles/{article_id}/read")

    resp = client.get("/api/articles?view=unread")
    assert resp.json() == []


def test_toggle_star(client):
    article_id = client.get("/api/articles").json()[0]["id"]

    resp = client.post(f"/api/articles/{article_id}/star")
    assert resp.json()["is_starred"] is True

    resp = client.post(f"/api/articles/{article_id}/star")
    assert resp.json()["is_starred"] is False


def test_toggle_read_flips_state(client):
    article_id = client.get("/api/articles").json()[0]["id"]

    resp = client.post(f"/api/articles/{article_id}/toggle-read")
    assert resp.json()["is_read"] is True

    resp = client.post(f"/api/articles/{article_id}/toggle-read")
    assert resp.json()["is_read"] is False


def test_image_proxy_rejects_missing_url(client):
    resp = client.get("/api/img")
    assert resp.status_code == 400


def test_image_proxy_rejects_non_http_scheme(client):
    resp = client.get("/api/img?url=file:///etc/passwd")
    assert resp.status_code == 400


def test_image_proxy_rejects_malformed_url(client):
    resp = client.get("/api/img?url=not-a-url")
    assert resp.status_code == 400


def test_add_source_via_structured_api(client):
    resp = client.post(
        "/api/sources",
        json={
            "key": "new-blog",
            "type": "rss",
            "title": "New Blog",
            "folder": "Test",
            "url": "https://newblog.example.com/feed.xml",
            "rules": [{"action": "include", "field": "title", "pattern": "(?i)ai"}],
        },
    )
    assert resp.status_code == 201

    sources = client.get("/api/sources").json()
    keys = {s["key"] for s in sources}
    assert "new-blog" in keys


def test_add_source_rejects_duplicate_key(client):
    resp = client.post(
        "/api/sources",
        json={"key": "s1", "type": "rss", "title": "Dup", "folder": "Test", "url": "https://x/feed"},
    )
    assert resp.status_code == 400


def test_add_source_rejects_invalid_regex(client):
    resp = client.post(
        "/api/sources",
        json={
            "key": "bad-regex-src",
            "type": "rss",
            "title": "Bad",
            "folder": "Test",
            "url": "https://x/feed",
            "rules": [{"action": "include", "field": "title", "pattern": "(unclosed"}],
        },
    )
    assert resp.status_code == 400


def test_add_source_rejects_missing_required_field(client):
    resp = client.post("/api/sources", json={"key": "incomplete"})
    assert resp.status_code == 400


def test_list_topics_reports_enabled_flag_and_topics(client):
    resp = client.get("/api/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "topics" in data


def test_generate_returns_404_when_llm_disabled(client):
    # The default `client` fixture's Config has llm.enabled=False (the
    # default) — generation must be unreachable while the kill switch is off.
    resp = client.post("/api/topics/whatever/generate")
    assert resp.status_code == 404


def test_generate_returns_202_and_spawns_worker_when_enabled(monkeypatch, tmp_path):
    from app.api import generate_api
    from app.config import Config, LLMSettings, Topic, Source, to_yaml
    from app.db import connect, init_schema
    from app.main import create_app
    from starlette.testclient import TestClient

    spawned = []
    monkeypatch.setattr(generate_api, "spawn_worker", lambda job_id: spawned.append(job_id))

    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)
    conn.close()

    config = Config(
        llm=LLMSettings(enabled=True, model="sonnet", timeout_minutes=10),
        topics=[Topic(key="ai-evals", title="AI Evals", folder="Generated", brief="track it")],
    )
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path)
    test_client = TestClient(app)

    resp = test_client.post("/api/topics/ai-evals/generate")

    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert spawned == [job_id]

    status_resp = test_client.get(f"/api/jobs/{job_id}")
    assert status_resp.json()["status"] == "queued"


def test_generate_returns_404_for_unknown_topic_even_when_enabled(monkeypatch, tmp_path):
    from app.api import generate_api
    from app.config import Config, LLMSettings, to_yaml
    from app.db import connect, init_schema
    from app.main import create_app
    from starlette.testclient import TestClient

    monkeypatch.setattr(generate_api, "spawn_worker", lambda job_id: None)

    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)
    conn.close()
    config = Config(llm=LLMSettings(enabled=True), topics=[])
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path)
    test_client = TestClient(app)

    resp = test_client.post("/api/topics/nonexistent/generate")
    assert resp.status_code == 404


def test_get_job_status_404_for_missing_job(client):
    resp = client.get("/api/jobs/9999")
    assert resp.status_code == 404
