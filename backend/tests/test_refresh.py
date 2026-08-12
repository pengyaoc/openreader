"""Tests for the synchronous refresh loop: fetch -> normalize -> rules ->
dedup -> persist, with a per-source report. Network is stubbed via a fake
fetcher so these run with zero I/O.
"""
from pathlib import Path

from app.config import Rule, Source
from app.connectors.http_fetch import FetchResult
from app.db import connect, init_schema
from app.ingest.refresh import refresh_all, refresh_gmail_source

FIXTURES = Path(__file__).parent / "fixtures"


def make_fetcher(responses: dict[str, bytes | Exception | None]):
    """responses maps source key -> fixture bytes, an Exception to raise,
    or None to simulate a 304 Not Modified."""

    def fetcher(source, etag, last_modified):
        result = responses[source.key]
        if isinstance(result, Exception):
            raise result
        if result is None:
            return FetchResult(304, None, etag, last_modified)
        return FetchResult(200, result, "etag-1", "last-mod-1")

    return fetcher


def test_refresh_persists_new_articles(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="s1", type="rss", title="Source 1", folder="Test", url="https://x/feed")
    fetcher = make_fetcher({"s1": (FIXTURES / "rss2.xml").read_bytes()})

    report = refresh_all(conn, [source], fetcher=fetcher)

    assert report["sources"][0]["status"] == "ok"
    assert report["sources"][0]["new"] == 2
    rows = conn.execute("SELECT title FROM articles ORDER BY id").fetchall()
    assert len(rows) == 2


def test_refresh_applies_exclude_rules(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(
        key="s1",
        type="rss",
        title="Source 1",
        folder="Test",
        url="https://x/feed",
        rules=[Rule(action="exclude", field="title", pattern="(?i)second")],
    )
    fetcher = make_fetcher({"s1": (FIXTURES / "rss2.xml").read_bytes()})

    report = refresh_all(conn, [source], fetcher=fetcher)

    assert report["sources"][0]["new"] == 1
    assert report["sources"][0]["filtered"] == 1
    rows = conn.execute("SELECT title FROM articles").fetchall()
    assert len(rows) == 1
    assert "First" in rows[0][0]


def test_refresh_is_idempotent_second_run_adds_nothing(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="s1", type="rss", title="Source 1", folder="Test", url="https://x/feed")
    body = (FIXTURES / "rss2.xml").read_bytes()
    fetcher = make_fetcher({"s1": body})

    refresh_all(conn, [source], fetcher=fetcher)
    report2 = refresh_all(conn, [source], fetcher=fetcher)

    assert report2["sources"][0]["new"] == 0
    rows = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert rows == 2


def test_refresh_handles_not_modified(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="s1", type="rss", title="Source 1", folder="Test", url="https://x/feed")
    fetcher = make_fetcher({"s1": None})

    report = refresh_all(conn, [source], fetcher=fetcher)

    assert report["sources"][0]["status"] == "not_modified"


def test_a_failing_source_does_not_block_others(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    s1 = Source(key="bad", type="rss", title="Bad", folder="Test", url="https://x/bad")
    s2 = Source(key="good", type="rss", title="Good", folder="Test", url="https://x/good")
    fetcher = make_fetcher(
        {"bad": ConnectionError("connect timeout"), "good": (FIXTURES / "atom.xml").read_bytes()}
    )

    report = refresh_all(conn, [s1, s2], fetcher=fetcher)

    statuses = {s["key"]: s["status"] for s in report["sources"]}
    assert statuses["bad"] == "error"
    assert statuses["good"] == "ok"
    rows = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert rows == 2  # from the good source


def test_refresh_uses_description_as_content_when_no_content_encoded(tmp_path):
    # Some feeds (dapenti.com/xilei among them) put the full HTML body,
    # images included, straight into <description> with no content:encoded
    # at all. content_html must not end up empty in that case.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="s1", type="rss", title="Source 1", folder="Test", url="https://x/feed")
    fetcher = make_fetcher({"s1": (FIXTURES / "rss2_description_only.xml").read_bytes()})

    refresh_all(conn, [source], fetcher=fetcher)

    row = conn.execute("SELECT content_html FROM articles").fetchone()
    assert "/api/img?url=https%3A%2F%2Fexample.com%2Fa.jpg" in row[0]
    assert "More text after the image" in row[0]


def test_source_error_is_recorded_on_the_source_row(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="bad", type="rss", title="Bad", folder="Test", url="https://x/bad")
    fetcher = make_fetcher({"bad": ConnectionError("connect timeout after 10s")})

    refresh_all(conn, [source], fetcher=fetcher)

    row = conn.execute("SELECT last_error FROM sources WHERE key='bad'").fetchone()
    assert row[0] and "timeout" in row[0]


def make_gmail_message(message_id: str, subject: str, html: str = "<p>body</p>"):
    import base64

    return {
        "id": message_id,
        "internalDate": "1754899824000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "Sender <sender@example.com>"},
            ],
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
                }
            ],
        },
    }


def test_refresh_gmail_source_persists_new_messages(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="gmail", title="Newsletters", folder="Test", query="label:x")

    messages = {"m1": make_gmail_message("m1", "First newsletter")}
    report = refresh_gmail_source(
        conn,
        source,
        access_token="fake-token",
        list_fn=lambda token, query: list(messages.keys()),
        get_fn=lambda token, mid: messages[mid],
    )

    assert report["status"] == "ok"
    assert report["new"] == 1
    row = conn.execute("SELECT title, origin FROM articles").fetchone()
    assert row[0] == "First newsletter"
    assert row[1] == "gmail"


def test_refresh_gmail_source_skips_already_fetched_message_ids_without_refetching(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="gmail", title="Newsletters", folder="Test", query="label:x")
    messages = {"m1": make_gmail_message("m1", "First newsletter")}
    get_calls = []

    def get_fn(token, mid):
        get_calls.append(mid)
        return messages[mid]

    refresh_gmail_source(
        conn, source, access_token="t", list_fn=lambda t, q: ["m1"], get_fn=get_fn
    )
    refresh_gmail_source(
        conn, source, access_token="t", list_fn=lambda t, q: ["m1"], get_fn=get_fn
    )

    assert get_calls == ["m1"]  # second run never re-fetched the message body


def test_refresh_gmail_source_applies_rules(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(
        key="newsletters",
        type="gmail",
        title="Newsletters",
        folder="Test",
        query="label:x",
        rules=[Rule(action="exclude", field="title", pattern="(?i)spam")],
    )
    messages = {
        "m1": make_gmail_message("m1", "Real newsletter"),
        "m2": make_gmail_message("m2", "This is Spam"),
    }
    report = refresh_gmail_source(
        conn,
        source,
        access_token="t",
        list_fn=lambda t, q: list(messages.keys()),
        get_fn=lambda t, mid: messages[mid],
    )

    assert report["new"] == 1
    assert report["filtered"] == 1


def test_refresh_gmail_source_isolates_a_single_bad_message(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="gmail", title="Newsletters", folder="Test", query="label:x")
    messages = {"m1": make_gmail_message("m1", "Good one")}

    def get_fn(token, mid):
        if mid == "bad":
            raise ConnectionError("boom")
        return messages[mid]

    report = refresh_gmail_source(
        conn,
        source,
        access_token="t",
        list_fn=lambda t, q: ["bad", "m1"],
        get_fn=get_fn,
    )

    assert report["new"] == 1
    row = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    assert row[0] == 1


def test_refresh_gmail_source_scopes_query_to_since_last_fetch(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="gmail", title="Newsletters", folder="Test", query="label:x")
    messages = {"m1": make_gmail_message("m1", "First newsletter")}
    queries_seen = []

    def list_fn(token, query):
        queries_seen.append(query)
        return list(messages.keys())

    refresh_gmail_source(conn, source, access_token="t", list_fn=list_fn, get_fn=lambda t, m: messages[m])
    # First-ever refresh: no last_fetched_at yet, so the configured query is
    # used as-is — nothing to scope against.
    assert queries_seen[0] == "label:x"

    refresh_gmail_source(conn, source, access_token="t", list_fn=list_fn, get_fn=lambda t, m: messages[m])
    # Second refresh: scoped to messages since the first refresh's recorded
    # last_fetched_at, not a re-walk of the whole configured query.
    assert queries_seen[1].startswith("label:x after:")


def test_refresh_gmail_source_records_error_when_list_fails(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="gmail", title="Newsletters", folder="Test", query="label:x")

    def failing_list(token, query):
        raise ConnectionError("auth expired")

    report = refresh_gmail_source(
        conn, source, access_token="t", list_fn=failing_list, get_fn=lambda t, m: {}
    )

    assert report["status"] == "error"
    row = conn.execute("SELECT last_error FROM sources WHERE key='newsletters'").fetchone()
    assert row[0] and "auth expired" in row[0]
