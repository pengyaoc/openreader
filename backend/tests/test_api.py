"""API-level tests using Starlette's TestClient against an in-memory-backed
app instance (temp DB + temp config per test).

Every create_app() call passes require_auth=False — these tests exercise
route/business logic, not the login flow itself (see test_auth.py for
that), and would otherwise all need a session cookie fixture just to
reach any endpoint.
"""
import pytest
from starlette.testclient import TestClient

from app.config import Config, Source
from app.db import connect, init_schema
from app.main import create_app


@pytest.fixture()
def client_multi(tmp_path):
    """Like `client`, but source s1 has two unread articles instead of
    one — needed for bulk mark-all-read tests, where a single-article
    fixture can't distinguish "marked everything" from "marked one thing
    that happened to be everything"."""
    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)

    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES (?, ?, ?, ?, ?)",
        ("s1", "rss", "Source One", "Test", "https://x/feed"),
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    for guid, title in (("g1", "Hello World"), ("g2", "Second Article")):
        conn.execute(
            """INSERT INTO articles
               (source_id, guid, url, title, excerpt, content_html, published_at, origin)
               VALUES (?, ?, ?, ?, 'An excerpt', '<p>Body</p>',
                       '2026-08-01T00:00:00Z', 'feed')""",
            (source_id, guid, f"https://x/{guid}", title),
        )
    conn.commit()
    conn.close()

    from app.config import to_yaml

    config = Config(sources=[Source(key="s1", type="rss", title="Source One", folder="Test", url="https://x/feed")])
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path, require_auth=False)
    return TestClient(app)


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
    app = create_app(db_path=db_path, config=config, config_path=config_path, require_auth=False)
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


def test_list_articles_omits_body_fields_but_flags_has_summary(client):
    # The list view only ever renders title/excerpt/meta (see
    # ArticleList.tsx) — content_html and llm_summary_html are dropped from
    # this response (app/store.py's _LIST_COLUMNS) so a page of articles
    # doesn't ship tens of KB of unused HTML per row. has_summary stands in
    # for llm_summary_html so the UI can still show a summary indicator.
    resp = client.get("/api/articles")
    row = resp.json()[0]
    assert "content_html" not in row
    assert "llm_summary_html" not in row
    assert row["has_summary"] is False

    article_id = row["id"]
    detail = client.get(f"/api/articles/{article_id}").json()
    assert detail["content_html"] == "<p>Body</p>"  # detail endpoint keeps the full body


def test_list_articles_limit_and_offset_are_defensively_parsed(client):
    # A bare int() cast on these query params previously 500'd on
    # non-numeric input and honored any limit, however large.
    resp = client.get("/api/articles?limit=not-a-number&offset=-5")
    assert resp.status_code == 200
    resp = client.get("/api/articles?limit=999999")
    assert resp.status_code == 200


def test_get_article_detail(client):
    resp = client.get("/api/articles")
    article_id = resp.json()[0]["id"]

    resp = client.get(f"/api/articles/{article_id}")
    assert resp.status_code == 200
    assert resp.json()["content_html"] == "<p>Body</p>"


def test_get_article_detail_includes_source_title(client):
    # Regression test: get_article() used to skip the sources JOIN that
    # list_articles() already had, so the reader had no source name to fall
    # back on for author-less articles and the byline area just went blank.
    article_id = client.get("/api/articles").json()[0]["id"]
    resp = client.get(f"/api/articles/{article_id}")
    assert resp.json()["source_title"] == "Source One"


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


def test_summarize_returns_404_when_llm_disabled(client):
    # The default `client` fixture's Config has llm.enabled=False (the
    # default) — summarization must be unreachable while the kill switch
    # is off, same posture as topic generation.
    article_id = client.get("/api/articles").json()[0]["id"]
    resp = client.post(f"/api/articles/{article_id}/summarize")
    assert resp.status_code == 404


def _client_with_llm_enabled(tmp_path):
    from app.config import Config, LLMSettings, Source, to_yaml
    from app.db import connect, init_schema
    from app.main import create_app

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
           VALUES (?, 'g1', 'https://x/1', 'Hello World', 'An excerpt',
                   '<p>Some real article body text here.</p>',
                   '2026-08-01T00:00:00Z', 'feed')""",
        (source_id,),
    )
    conn.commit()
    conn.close()

    config = Config(
        llm=LLMSettings(enabled=True),
        sources=[Source(key="s1", type="rss", title="Source One", folder="Test", url="https://x/feed")],
    )
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path, require_auth=False)
    return TestClient(app)


def test_summarize_404_for_unknown_article(monkeypatch, tmp_path):
    from app.api import articles

    monkeypatch.setattr(articles, "summarize_text", lambda *a, **k: "<p>should not be called</p>")
    test_client = _client_with_llm_enabled(tmp_path)

    resp = test_client.post("/api/articles/9999/summarize")
    assert resp.status_code == 404


def test_summarize_generates_and_persists_summary(monkeypatch, tmp_path):
    from app.api import articles

    calls = []

    def fake_summarize_text(text, word_count, **kwargs):
        calls.append((text, word_count))
        return "<ul><li>Key point</li></ul>"

    monkeypatch.setattr(articles, "summarize_text", fake_summarize_text)
    test_client = _client_with_llm_enabled(tmp_path)
    article_id = test_client.get("/api/articles").json()[0]["id"]

    resp = test_client.post(f"/api/articles/{article_id}/summarize")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary_html"] == "<ul><li>Key point</li></ul>"
    assert data["llm_summary_at"]
    assert len(calls) == 1
    assert calls[0][0] == "Some real article body text here."

    detail = test_client.get(f"/api/articles/{article_id}").json()
    assert detail["llm_summary_html"] == "<ul><li>Key point</li></ul>"
    assert detail["llm_summary_at"] == data["llm_summary_at"]


def test_summarize_returns_cached_summary_without_calling_runner_again(monkeypatch, tmp_path):
    from app.api import articles

    calls = []
    monkeypatch.setattr(
        articles, "summarize_text", lambda text, word_count, **k: calls.append(1) or "<p>fresh</p>"
    )
    test_client = _client_with_llm_enabled(tmp_path)
    article_id = test_client.get("/api/articles").json()[0]["id"]

    first = test_client.post(f"/api/articles/{article_id}/summarize")
    assert first.json()["summary_html"] == "<p>fresh</p>"
    assert len(calls) == 1

    second = test_client.post(f"/api/articles/{article_id}/summarize")
    assert second.json()["summary_html"] == "<p>fresh</p>"
    assert len(calls) == 1  # not invoked again — cached path


def test_summarize_returns_503_on_claude_error_and_persists_nothing(monkeypatch, tmp_path):
    from app.api import articles
    from app.summarize import ClaudeError

    def failing_summarize_text(text, word_count, **kwargs):
        raise ClaudeError("claude exited 1: boom")

    monkeypatch.setattr(articles, "summarize_text", failing_summarize_text)
    test_client = _client_with_llm_enabled(tmp_path)
    article_id = test_client.get("/api/articles").json()[0]["id"]

    resp = test_client.post(f"/api/articles/{article_id}/summarize")
    assert resp.status_code == 503

    detail = test_client.get(f"/api/articles/{article_id}").json()
    assert detail["llm_summary_html"] is None


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


def test_llm_status_reports_disabled_by_default(client):
    resp = client.get("/api/llm-status")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_llm_status_reports_enabled_when_configured(tmp_path):
    from app.config import Config, LLMSettings, to_yaml
    from app.db import connect, init_schema
    from app.main import create_app
    from starlette.testclient import TestClient

    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)
    conn.close()
    config = Config(llm=LLMSettings(enabled=True))
    config_path = tmp_path / "feeds.yaml"
    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path, require_auth=False)
    test_client = TestClient(app)

    resp = test_client.get("/api/llm-status")
    assert resp.json() == {"enabled": True}


def test_put_config_updates_the_config_file(client, tmp_path):
    new_yaml = "sources:\n  - key: s1\n    type: rss\n    title: Renamed\n    folder: Test\n    url: https://x/feed\n"
    resp = client.put("/api/config", json={"yaml": new_yaml})
    assert resp.status_code == 200

    resp = client.get("/api/config")
    assert "Renamed" in resp.json()["yaml"]


def test_put_config_is_blocked_when_readonly(client, monkeypatch):
    # READER_READONLY_CONFIG is set on the VM deployment specifically
    # because this endpoint is otherwise unauthenticated — an internet-
    # reachable arbitrary write to feeds.yaml (and, via app.state.config,
    # a live flip of llm.enabled back on, defeating its kill switch).
    from app import settings

    monkeypatch.setattr(settings, "READONLY_CONFIG", True)

    original = client.get("/api/config").json()["yaml"]
    resp = client.put("/api/config", json={"yaml": "sources: []\n"})

    assert resp.status_code == 403
    assert client.get("/api/config").json()["yaml"] == original  # untouched


def test_mark_all_read_marks_every_unread_article_on_the_source(client_multi):
    source_id = client_multi.get("/api/sources").json()[0]["id"]

    resp = client_multi.post(f"/api/sources/{source_id}/mark-all-read")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "marked": 2}

    articles = client_multi.get("/api/articles").json()
    assert all(a["is_read"] for a in articles)

    sources = client_multi.get("/api/sources").json()
    assert sources[0]["unread_count"] == 0


def test_mark_all_read_leaves_already_read_articles_alone_and_is_idempotent(client_multi):
    source_id = client_multi.get("/api/sources").json()[0]["id"]
    article_id = client_multi.get("/api/articles").json()[0]["id"]
    client_multi.post(f"/api/articles/{article_id}/read")  # pre-mark one

    resp = client_multi.post(f"/api/sources/{source_id}/mark-all-read")
    assert resp.json() == {"ok": True, "marked": 1}  # only the other one

    resp2 = client_multi.post(f"/api/sources/{source_id}/mark-all-read")
    assert resp2.json() == {"ok": True, "marked": 0}  # nothing left to mark


def test_mark_all_read_404_for_missing_source(client):
    resp = client.post("/api/sources/9999/mark-all-read")
    assert resp.status_code == 404


def test_get_article_hydrates_full_text_via_the_threaded_fetch_path(tmp_path, monkeypatch):
    # Regression test for the asyncio.to_thread wrapper in articles.py:
    # hydrate_article's fetch is synchronous (httpx.get, up to a 5s
    # timeout) and used to run directly on the event loop from this async
    # handler, blocking every other in-flight request for its duration.
    # This exercises the real HTTP -> threaded-hydrate -> its-own-SQLite-
    # connection path end to end (not just hydrate_article() in isolation,
    # which test_hydrate.py already covers) to catch exactly the kind of
    # cross-thread SQLite misuse that fix was at risk of introducing.
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
           VALUES (?, 'g1', 'https://x/full-article', 'Short One', 'short excerpt', '<p>short</p>',
                   '2026-08-01T00:00:00Z', 'feed')""",
        (source_id,),
    )
    conn.commit()
    conn.close()

    config = Config(
        sources=[
            Source(
                key="s1", type="rss", title="Source One", folder="Test",
                url="https://x/feed", fetch_full_text=True,
            )
        ]
    )
    config_path = tmp_path / "feeds.yaml"
    from app.config import to_yaml

    config_path.write_text(to_yaml(config))
    app = create_app(db_path=db_path, config=config, config_path=config_path, require_auth=False)
    client = TestClient(app)

    from pathlib import Path as _Path

    fixture_html = (_Path(__file__).parent / "fixtures" / "article_page.html").read_text()

    class FakeResponse:
        text = fixture_html

        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.ingest.hydrate.httpx.get", lambda *a, **kw: FakeResponse())

    article_id = client.get("/api/articles").json()[0]["id"]
    resp = client.get(f"/api/articles/{article_id}")

    assert resp.status_code == 200
    assert "first real paragraph" in resp.json()["content_html"]
    assert resp.json()["hydrated_at"] is not None


def test_mark_all_read_articles_marks_unread_across_every_source(tmp_path):
    # Distinct from test_mark_all_read_marks_every_unread_article_on_the_source
    # above (sources.mark_all_read, scoped to one source) — this is the
    # Unread-saved-view-level bulk action, articles.mark_all_read, spanning
    # every source at once. Two sources needed to actually prove "every",
    # not just "the one source client_multi happens to seed".
    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('s1', 'rss', 'Source One', 'Test', 'https://x/feed')"
    )
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('s2', 'rss', 'Source Two', 'Test', 'https://x/feed2')"
    )
    s1 = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    s2 = conn.execute("SELECT id FROM sources WHERE key='s2'").fetchone()[0]
    conn.execute(
        "INSERT INTO articles (source_id, guid, url, title, excerpt, content_html, published_at, origin) "
        "VALUES (?, 'g1', 'https://x/1', 'A1', 'e', '<p>b</p>', '2026-08-01T00:00:00Z', 'feed')",
        (s1,),
    )
    conn.execute(
        "INSERT INTO articles (source_id, guid, url, title, excerpt, content_html, published_at, origin) "
        "VALUES (?, 'g2', 'https://x/2', 'A2', 'e', '<p>b</p>', '2026-08-01T00:00:00Z', 'feed')",
        (s2,),
    )
    conn.commit()
    conn.close()

    config = Config(
        sources=[
            Source(key="s1", type="rss", title="Source One", folder="Test", url="https://x/feed"),
            Source(key="s2", type="rss", title="Source Two", folder="Test", url="https://x/feed2"),
        ]
    )
    config_path = tmp_path / "feeds.yaml"
    from app.config import to_yaml

    config_path.write_text(to_yaml(config))
    client = TestClient(create_app(db_path=db_path, config=config, config_path=config_path, require_auth=False))

    resp = client.post("/api/articles/mark-all-read")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "marked": 2}

    articles = client.get("/api/articles").json()
    assert all(a["is_read"] for a in articles)

    sources = client.get("/api/sources").json()
    assert all(s["unread_count"] == 0 for s in sources)

    # Idempotent — nothing left to mark on a second call.
    resp2 = client.post("/api/articles/mark-all-read")
    assert resp2.json() == {"ok": True, "marked": 0}


def test_mark_all_read_route_does_not_collide_with_article_id_route(client):
    # /api/articles/mark-all-read must be matched by its own literal route,
    # not fall through to /api/articles/{article_id} and try int("mark-all-read").
    resp = client.post("/api/articles/mark-all-read")
    assert resp.status_code == 200


def test_removing_a_source_from_config_hides_it_from_sidebar_but_keeps_its_articles(client):
    # End-to-end version of the reported bug: removing a source from
    # feeds.yaml (via PUT /api/config) must make it disappear from
    # GET /api/sources (the sidebar) immediately, without deleting its
    # already-fetched articles — they stay reachable by id and get marked
    # read by reconcile_read_state (covered separately in test_refresh.py).
    sources_before = client.get("/api/sources").json()
    assert any(s["key"] == "s1" for s in sources_before)
    article_id = client.get("/api/articles").json()[0]["id"]

    resp = client.put("/api/config", json={"yaml": "sources: []\n"})
    assert resp.status_code == 200
    assert resp.json()["reconciled"] == 1  # the one seeded article, now read

    sources_after = client.get("/api/sources").json()
    assert sources_after == []  # gone from the sidebar

    detail = client.get(f"/api/articles/{article_id}")
    assert detail.status_code == 200  # still in the DB, still directly reachable
    assert detail.json()["is_read"] is True


def test_update_source_edits_title_folder_and_url(client):
    source_id = client.get("/api/sources").json()[0]["id"]

    resp = client.put(
        f"/api/sources/{source_id}",
        json={
            "title": "Renamed Source",
            "folder": "New Folder",
            "url": "https://x/new-feed",
            "fetch_full_text": True,
            "rules": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    sources = client.get("/api/sources").json()
    assert sources[0]["title"] == "Renamed Source"
    assert sources[0]["folder"] == "New Folder"

    config_yaml = client.get("/api/config").json()["yaml"]
    assert "https://x/new-feed" in config_yaml
    assert "fetch_full_text: true" in config_yaml


def test_update_source_ignores_key_and_type_in_request_body(client):
    # key/type are identity, locked to the existing entry regardless of
    # what the request sends — changing either would orphan dedup/history.
    source_id = client.get("/api/sources").json()[0]["id"]

    resp = client.put(
        f"/api/sources/{source_id}",
        json={
            "key": "attempted-rename",
            "type": "imap",
            "title": "Still RSS",
            "folder": "Test",
            "url": "https://x/feed",
        },
    )
    assert resp.status_code == 200

    sources = client.get("/api/sources").json()
    assert sources[0]["key"] == "s1"  # unchanged
    assert sources[0]["type"] == "rss"  # unchanged


def test_update_source_404_for_missing_id(client):
    resp = client.put("/api/sources/9999", json={"title": "X", "folder": "F", "url": "https://x"})
    assert resp.status_code == 404


def test_update_source_rejects_invalid_regex(client):
    source_id = client.get("/api/sources").json()[0]["id"]
    resp = client.put(
        f"/api/sources/{source_id}",
        json={
            "title": "S",
            "folder": "F",
            "url": "https://x/feed",
            "rules": [{"action": "include", "field": "title", "pattern": "(unclosed"}],
        },
    )
    assert resp.status_code == 400


def test_update_source_reconciles_read_state_when_rules_tighten(client):
    source_id = client.get("/api/sources").json()[0]["id"]
    # Seeded article's title is "Hello World" — a rule requiring "xyz" in
    # the title makes it fail immediately.
    resp = client.put(
        f"/api/sources/{source_id}",
        json={
            "title": "Source One",
            "folder": "Test",
            "url": "https://x/feed",
            "rules": [{"action": "include", "field": "title", "pattern": "xyz"}],
        },
    )
    assert resp.json()["reconciled"] == 1

    article = client.get("/api/articles").json()[0]
    assert article["is_read"] is True


def test_remove_source_hides_it_but_keeps_its_articles(client):
    source_id = client.get("/api/sources").json()[0]["id"]
    article_id = client.get("/api/articles").json()[0]["id"]

    resp = client.delete(f"/api/sources/{source_id}")
    assert resp.status_code == 200
    assert resp.json()["reconciled"] == 1

    assert client.get("/api/sources").json() == []  # gone from sidebar

    detail = client.get(f"/api/articles/{article_id}")
    assert detail.status_code == 200  # still reachable directly
    assert detail.json()["is_read"] is True

    config_yaml = client.get("/api/config").json()["yaml"]
    assert "s1" not in config_yaml


def test_remove_source_404_for_missing_id(client):
    resp = client.delete("/api/sources/9999")
    assert resp.status_code == 404


def test_add_source_accepts_imap_type(client):
    resp = client.post(
        "/api/sources",
        json={
            "key": "a-newsletter",
            "type": "imap",
            "title": "A Newsletter",
            "folder": "Newsletters",
            "query": "from:sender@example.com newer_than:30d",
        },
    )
    assert resp.status_code == 201

    sources = client.get("/api/sources").json()
    keys = {s["key"]: s["type"] for s in sources}
    assert keys["a-newsletter"] == "imap"


def test_write_endpoints_respect_readonly_config(client, monkeypatch):
    from app import settings

    monkeypatch.setattr(settings, "READONLY_CONFIG", True)
    source_id = client.get("/api/sources").json()[0]["id"]

    assert client.post("/api/sources", json={"key": "x", "type": "rss", "title": "X", "folder": "F", "url": "https://x"}).status_code == 403
    assert client.put(f"/api/sources/{source_id}", json={"title": "X", "folder": "F", "url": "https://x"}).status_code == 403
    assert client.delete(f"/api/sources/{source_id}").status_code == 403


def test_get_source_returns_full_config_detail(client):
    source_id = client.get("/api/sources").json()[0]["id"]
    resp = client.get(f"/api/sources/{source_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "s1"
    assert body["url"] == "https://x/feed"
    assert body["rules"] == []
    assert "unread_count" in body


def test_get_source_404_for_missing_id(client):
    resp = client.get("/api/sources/9999")
    assert resp.status_code == 404
