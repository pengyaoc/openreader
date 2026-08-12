"""Tests for lazy, per-article full-text hydration: fetch + extract only
happens once, on demand, with a summary fallback on any failure.
"""
from pathlib import Path

from app.db import connect, init_schema
from app.ingest.hydrate import hydrate_article

FIXTURES = Path(__file__).parent / "fixtures"


def seed_article(conn, *, content_html="", url="https://example.com/post"):
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('s1', 'rss', 'S', 'F', 'https://x')"
    )
    source_id = conn.execute("SELECT id FROM sources").fetchone()[0]
    conn.execute(
        """INSERT INTO articles (source_id, guid, url, title, excerpt, content_html, origin)
           VALUES (?, 'g1', ?, 'Title', 'short excerpt', ?, 'feed')""",
        (source_id, url, content_html),
    )
    conn.commit()
    return conn.execute("SELECT id FROM articles").fetchone()[0]


def test_hydrate_fetches_and_stores_content_when_truncated(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn)
    page_html = (FIXTURES / "article_page.html").read_text()

    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        return page_html

    result = hydrate_article(conn, article_id, fetch_full_text=True, fetcher=fetcher)

    assert len(calls) == 1
    assert "first real paragraph" in result["content_html"]
    row = conn.execute(
        "SELECT content_html, hydrated_at FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    assert "first real paragraph" in row[0]
    assert row[1] is not None


def test_hydrate_is_a_noop_on_second_call(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn)
    page_html = (FIXTURES / "article_page.html").read_text()
    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        return page_html

    hydrate_article(conn, article_id, fetch_full_text=True, fetcher=fetcher)
    hydrate_article(conn, article_id, fetch_full_text=True, fetcher=fetcher)

    assert len(calls) == 1  # second call must not re-fetch


def test_hydrate_skips_when_content_already_substantial(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn, content_html="<p>" + ("word " * 200) + "</p>")
    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        return "<html><body><article><p>should not be used</p></article></body></html>"

    hydrate_article(conn, article_id, fetch_full_text=True, fetcher=fetcher)

    assert len(calls) == 0


def test_hydrate_skips_when_fetch_full_text_disabled(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn)
    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        return "<html></html>"

    hydrate_article(conn, article_id, fetch_full_text=False, fetcher=fetcher)

    assert len(calls) == 0


def test_hydrate_falls_back_to_excerpt_on_fetch_failure(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn)

    def fetcher(url, timeout):
        raise ConnectionError("timed out")

    result = hydrate_article(conn, article_id, fetch_full_text=True, fetcher=fetcher)

    assert result["content_html"] == ""  # caller falls back to excerpt
    row = conn.execute(
        "SELECT hydrate_failed_at FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    assert row[0] is not None


def test_hydrate_resolves_relative_image_urls_against_article_url(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn, url="https://blog.example.com/posts/1")
    page_html = """
    <html><body><article>
      <p>Long enough first paragraph to be picked up by the density scorer here.</p>
      <img src="/images/photo.png"/>
      <p>Long enough second paragraph to keep this the winning content block.</p>
    </article></body></html>
    """

    result = hydrate_article(
        conn, article_id, fetch_full_text=True, fetcher=lambda url, timeout: page_html
    )

    # Relative -> absolute resolution AND the image proxy rewrite both apply:
    # the proxied URL should wrap the fully-resolved absolute image URL.
    assert "/api/img?url=https%3A%2F%2Fblog.example.com%2Fimages%2Fphoto.png" in result["content_html"]


def test_hydrate_does_not_retry_after_a_recorded_failure(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    article_id = seed_article(conn)
    calls = []

    def failing_fetcher(url, timeout):
        calls.append(url)
        raise ConnectionError("timed out")

    hydrate_article(conn, article_id, fetch_full_text=True, fetcher=failing_fetcher)
    hydrate_article(conn, article_id, fetch_full_text=True, fetcher=failing_fetcher)

    assert len(calls) == 1
