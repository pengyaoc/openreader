"""Tests for the synchronous refresh loop: fetch -> normalize -> rules ->
dedup -> persist, with a per-source report. Network is stubbed via a fake
fetcher so these run with zero I/O.
"""
from pathlib import Path

from app.config import Config, Rule, Source
from app.connectors.http_fetch import FetchResult
from app.db import connect, init_schema
from app.ingest.refresh import (
    reconcile_read_state,
    refresh_all,
    refresh_gmail_source,
    refresh_imap_source,
)

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


def make_imap_message(message_id: str, subject: str, html: str = "<p>body</p>") -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Message-Id"] = f"<{message_id}@newsletter.example.com>"
    msg["Subject"] = subject
    msg["From"] = "Sender <sender@example.com>"
    msg["Date"] = "Tue, 11 Aug 2026 05:30:24 +0000"
    msg.set_content(html, subtype="html")
    return bytes(msg)


def test_refresh_imap_source_persists_new_messages(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="imap", title="Newsletters", folder="Test", query="")

    messages = {"1": make_imap_message("m1", "First newsletter")}
    report = refresh_imap_source(
        conn,
        source,
        imap_client=object(),  # unused directly — search_fn/fetch_fn are injected below
        search_fn=lambda client, folder, **kw: list(messages.keys()),
        fetch_fn=lambda client, mid: messages[mid],
    )

    assert report["status"] == "ok"
    assert report["new"] == 1
    row = conn.execute("SELECT title, origin FROM articles").fetchone()
    assert row[0] == "First newsletter"
    assert row[1] == "gmail"  # reuses the gmail origin category for mail-sourced articles


def test_refresh_imap_source_dedupes_by_message_guid_even_when_refetched(tmp_path):
    # Unlike Gmail's stable message id, IMAP sequence/UID numbers aren't a
    # reliable cross-session guid, so refresh_imap_source can't skip a
    # FETCH the way refresh_gmail_source skips a messages.get — it dedupes
    # only after parsing, on the message's own Message-Id. That means a
    # second refresh will refetch, but must still land zero new rows.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="imap", title="Newsletters", folder="Test", query="")
    messages = {"1": make_imap_message("m1", "First newsletter")}

    refresh_imap_source(
        conn, source, imap_client=object(),
        search_fn=lambda client, folder, **kw: ["1"],
        fetch_fn=lambda client, mid: messages[mid],
    )
    refresh_imap_source(
        conn, source, imap_client=object(),
        search_fn=lambda client, folder, **kw: ["1"],
        fetch_fn=lambda client, mid: messages[mid],
    )

    row = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    assert row[0] == 1


def test_refresh_imap_source_applies_rules(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(
        key="newsletters",
        type="imap",
        title="Newsletters",
        folder="Test",
        query="",
        rules=[Rule(action="exclude", field="title", pattern="(?i)spam")],
    )
    messages = {
        "1": make_imap_message("m1", "Real newsletter"),
        "2": make_imap_message("m2", "This is Spam"),
    }
    report = refresh_imap_source(
        conn, source, imap_client=object(),
        search_fn=lambda client, folder, **kw: list(messages.keys()),
        fetch_fn=lambda client, mid: messages[mid],
    )

    assert report["new"] == 1
    assert report["filtered"] == 1


def test_refresh_imap_source_isolates_a_single_bad_message(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="imap", title="Newsletters", folder="Test", query="")
    messages = {"1": make_imap_message("m1", "Good one")}

    def fetch_fn(client, mid):
        if mid == "bad":
            raise ConnectionError("boom")
        return messages[mid]

    report = refresh_imap_source(
        conn, source, imap_client=object(),
        search_fn=lambda client, folder, **kw: ["bad", "1"],
        fetch_fn=fetch_fn,
    )

    assert report["new"] == 1
    row = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    assert row[0] == 1


def test_refresh_imap_source_scopes_since_to_last_fetch(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="imap", title="Newsletters", folder="Test", query="")
    messages = {"1": make_imap_message("m1", "First newsletter")}
    sinces_seen = []

    def search_fn(client, folder, since=None, **kw):
        sinces_seen.append(since)
        return list(messages.keys())

    refresh_imap_source(conn, source, imap_client=object(), search_fn=search_fn, fetch_fn=lambda c, m: messages[m])
    refresh_imap_source(conn, source, imap_client=object(), search_fn=search_fn, fetch_fn=lambda c, m: messages[m])

    # First refresh has no last_fetched_at yet, so `since` falls back to the
    # default window; second refresh scopes to just after the first's
    # recorded last_fetched_at — strictly later than the first `since`.
    assert sinces_seen[1] > sinces_seen[0]


def test_refresh_imap_source_records_error_when_search_fails(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="imap", title="Newsletters", folder="Test", query="")

    def failing_search(client, folder, **kw):
        raise ConnectionError("login expired")

    report = refresh_imap_source(
        conn, source, imap_client=object(), search_fn=failing_search, fetch_fn=lambda c, m: {}
    )

    assert report["status"] == "error"
    row = conn.execute("SELECT last_error FROM sources WHERE key='newsletters'").fetchone()
    assert row[0] and "login expired" in row[0]


def test_refresh_imap_source_uses_configured_mailbox_folder(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(
        key="newsletters", type="imap", title="Newsletters", folder="Test",
        query="", mailbox_folder="Newsletters",
    )
    folders_seen = []

    def search_fn(client, folder, **kw):
        folders_seen.append(folder)
        return []

    refresh_imap_source(conn, source, imap_client=object(), search_fn=search_fn, fetch_fn=lambda c, m: {})
    assert folders_seen == ["Newsletters"]


def test_refresh_all_reports_error_when_imap_not_configured(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="newsletters", type="imap", title="Newsletters", folder="Test", query="")

    report = refresh_all(conn, [source], fetcher=lambda *a: None, imap_client=None)
    assert report["sources"][0]["status"] == "error"
    assert "IMAP not configured" in report["sources"][0]["error"]


def _atom_feed(entry_id: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>{entry_id}</id>
    <title>Same Article, Drifting Id</title>
    <link href="https://example.com/post/1" rel="alternate"/>
    <updated>2026-08-01T12:00:00Z</updated>
    <summary>Body text.</summary>
  </entry>
</feed>""".encode()


def test_refresh_dedupes_via_content_hash_when_a_feeds_guid_format_drifts(tmp_path):
    # Regression test: simonwillison.net's Atom feed started appending a
    # #atom-everything fragment to entry <id> values it previously served
    # bare, on a later refresh — a real guid, but a *different* one for the
    # same article, causing ~30 duplicated articles in production (found
    # 2026-08-13). guid alone can't catch this; content_hash (built from a
    # fragment-stripped canonical URL) is meant to.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    source = Source(key="s1", type="rss", title="Source 1", folder="Test", url="https://x/feed")

    # First refresh: bare id, no ETag change forced — simulate two separate
    # 200 responses (conditional GET returning fresh content both times,
    # which is what actually happens when a feed's content changes).
    fetcher_v1 = make_fetcher({"s1": _atom_feed("https://example.com/post/1")})
    report1 = refresh_all(conn, [source], fetcher=fetcher_v1)
    assert report1["sources"][0]["new"] == 1

    fetcher_v2 = make_fetcher({"s1": _atom_feed("https://example.com/post/1#atom-everything")})
    report2 = refresh_all(conn, [source], fetcher=fetcher_v2)
    assert report2["sources"][0]["new"] == 0  # same article, not a duplicate

    rows = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert rows == 1


def _seed_article(conn, source_id, guid, title, is_read=0):
    conn.execute(
        """INSERT INTO articles
           (source_id, guid, url, title, excerpt, content_html, published_at, origin, is_read)
           VALUES (?, ?, ?, ?, '', '', '2026-08-01T00:00:00Z', 'feed', ?)""",
        (source_id, guid, f"https://x/{guid}", title, is_read),
    )


def test_reconcile_marks_articles_read_when_source_removed_from_config(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('uber-eng', 'rss', 'Uber Eng', 'Eng', 'https://x')"
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='uber-eng'").fetchone()[0]
    _seed_article(conn, source_id, "g1", "Post A")
    _seed_article(conn, source_id, "g2", "Post B")
    conn.commit()

    # New config no longer has uber-eng at all.
    new_config = Config(sources=[])
    marked = reconcile_read_state(conn, new_config)

    assert marked == 2
    rows = conn.execute("SELECT is_read FROM articles").fetchall()
    assert all(r[0] == 1 for r in rows)


def test_reconcile_marks_articles_read_when_they_no_longer_pass_tightened_rules(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('xilei', 'rss', 'Xilei', 'Reading', 'https://x')"
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='xilei'").fetchone()[0]
    _seed_article(conn, source_id, "g1", "喷嚏图卦 today")  # matched old pattern only
    _seed_article(conn, source_id, "g2", "【喷嚏图卦 today")  # matches new pattern too
    conn.commit()

    new_config = Config(
        sources=[
            Source(
                key="xilei", type="rss", title="Xilei", folder="Reading", url="https://x",
                rules=[Rule(action="include", field="title", pattern="【喷嚏图卦")],
            )
        ]
    )
    marked = reconcile_read_state(conn, new_config)

    assert marked == 1
    rows = {r[0]: r[1] for r in conn.execute("SELECT guid, is_read FROM articles")}
    assert rows["g1"] == 1  # no longer matches -> marked read
    assert rows["g2"] == 0  # still matches -> left alone


def test_reconcile_never_touches_already_read_articles(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('s1', 'rss', 'S', 'F', 'https://x')"
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    _seed_article(conn, source_id, "g1", "Anything", is_read=1)
    conn.commit()
    read_at_before = conn.execute("SELECT read_at FROM articles WHERE guid='g1'").fetchone()[0]

    marked = reconcile_read_state(conn, Config(sources=[]))  # source removed entirely

    assert marked == 0  # already read, nothing to do
    read_at_after = conn.execute("SELECT read_at FROM articles WHERE guid='g1'").fetchone()[0]
    assert read_at_after == read_at_before  # untouched, not re-stamped


def test_reconcile_is_idempotent(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, url) VALUES ('s1', 'rss', 'S', 'F', 'https://x')"
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key='s1'").fetchone()[0]
    _seed_article(conn, source_id, "g1", "Post")
    conn.commit()

    new_config = Config(sources=[])
    assert reconcile_read_state(conn, new_config) == 1
    assert reconcile_read_state(conn, new_config) == 0  # already handled
