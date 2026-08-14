"""Tests for the synchronous refresh loop: fetch -> normalize -> rules ->
dedup -> persist, with a per-source report. Network is stubbed via a fake
fetcher so these run with zero I/O.
"""
from pathlib import Path

from app.config import Config, Rule, Source
from app.connectors.http_fetch import FetchResult
from app.db import connect, init_schema
from app.ingest.refresh import (
    _refresh_imap_sequential,
    reconcile_read_state,
    refresh_all,
    refresh_imap_source,
    refresh_imap_sources,
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
    assert row[1] == "email"


def test_refresh_imap_source_dedupes_by_message_guid_even_when_refetched(tmp_path):
    # IMAP sequence/UID numbers aren't a reliable cross-session guid, so
    # refresh_imap_source can't skip a FETCH just because it's seen the
    # message_id before — it dedupes only after parsing, on the message's
    # own Message-Id. That means a second refresh will refetch, but must
    # still land zero new rows.
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

    report = refresh_all(conn, [source], fetcher=lambda *a: None, imap_connect=None)
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


def test_rss_sources_fetch_concurrently_not_sequentially(tmp_path):
    # The actual point of the batching change: N sources whose fetcher each
    # takes T seconds must finish in roughly T total (bounded by the pool),
    # not N*T (what the old sequential loop did — measured live, 2026-08-13:
    # 9 real sources summed to ~7.7s with no single dominant outlier).
    import time as time_module

    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    n = 5
    delay = 0.3
    sources = [
        Source(key=f"s{i}", type="rss", title=f"S{i}", folder="Test", url=f"https://x/{i}")
        for i in range(n)
    ]

    def slow_fetcher(source, etag, last_modified):
        time_module.sleep(delay)
        return FetchResult(304, None, etag, last_modified)

    started = time_module.monotonic()
    report = refresh_all(conn, sources, fetcher=slow_fetcher)
    elapsed = time_module.monotonic() - started

    assert len(report["sources"]) == n
    assert all(s["status"] == "not_modified" for s in report["sources"])
    # Sequential would take n*delay = 1.5s; concurrent (pool of 6) should
    # land close to one delay's worth. Generous ceiling to avoid flakiness
    # on a loaded CI box while still clearly distinguishing the two shapes.
    assert elapsed < n * delay * 0.6


def test_refresh_all_preserves_source_order_across_mixed_types(tmp_path):
    # refresh_all reassembles RSS-batch results (keyed by source key, no
    # inherent order from the thread pool) back into the caller's original
    # per-source order — this proves that reassembly is correct even when
    # rss/imap sources are interleaved in the config, not just when they're
    # grouped by type.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    sources = [
        Source(key="rss-a", type="rss", title="A", folder="F", url="https://x/a"),
        Source(key="newsletters", type="imap", title="N", folder="F", query=""),
        Source(key="rss-b", type="rss", title="B", folder="F", url="https://x/b"),
    ]
    fetcher = make_fetcher({"rss-a": None, "rss-b": None})  # both 304

    report = refresh_all(
        conn, sources, fetcher=fetcher,
        imap_connect=None,  # reports "IMAP not configured" without a real connection
    )

    keys_in_order = [s["key"] for s in report["sources"]]
    assert keys_in_order == ["rss-a", "newsletters", "rss-b"]


def test_imap_refresh_connects_once_and_reuses_it_across_sources(tmp_path):
    # 2026-08-13: reverted from per-source concurrent connections back to
    # one shared connection reused sequentially — a fresh Gmail login per
    # IMAP source, every refresh, from one datacenter IP got silently
    # throttled by Gmail's abuse heuristics live on the VM. connect_fn
    # must be called exactly once no matter how many IMAP sources refresh.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    n = 5
    sources = [
        Source(key=f"imap{i}", type="imap", title=f"I{i}", folder="Test", query="")
        for i in range(n)
    ]

    connect_calls = 0

    class FakeClient:
        def logout(self):
            pass

    def connect_fn():
        nonlocal connect_calls
        connect_calls += 1
        return FakeClient()

    reports = _refresh_imap_sequential(
        conn, sources, connect_fn,
        search_fn=lambda client, folder, **kw: [],
        fetch_fn=lambda client, mid: b"",
    )

    assert connect_calls == 1
    assert len(reports) == n
    assert all(r["status"] == "ok" for r in reports.values())


def test_imap_refresh_persists_correctly_across_multiple_sources(tmp_path):
    # End-to-end correctness of the sequential path (not just the
    # connect-once behavior): search results and messages from a single
    # shared client all land in the right source's articles with the
    # right counts.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    sources = [
        Source(key="imapA", type="imap", title="A", folder="Test", query="", mailbox_folder="InboxA"),
        Source(key="imapB", type="imap", title="B", folder="Test", query="", mailbox_folder="InboxB"),
    ]
    raw_messages = {
        "a1": make_imap_message("a1", "From A"),
        "b1": make_imap_message("b1", "From B"),
        "b2": make_imap_message("b2", "Also From B"),
    }
    ids_by_mailbox = {"InboxA": ["a1"], "InboxB": ["b1", "b2"]}

    class FakeClient:
        def logout(self):
            pass

    def connect_fn():
        return FakeClient()

    # Dispatches on the mailbox folder passed through from each source,
    # not call order — the shared client can't identify which source is
    # calling, but each source's own mailbox_folder can.
    def search_fn(client, folder, **kw):
        return ids_by_mailbox[folder]

    def fetch_fn(client, message_id):
        return raw_messages[message_id]

    reports = _refresh_imap_sequential(conn, sources, connect_fn, search_fn=search_fn, fetch_fn=fetch_fn)

    assert reports["imapA"]["new"] == 1
    assert reports["imapB"]["new"] == 2

    rows = conn.execute(
        "SELECT s.key, a.title FROM articles a JOIN sources s ON s.id = a.source_id ORDER BY a.title"
    ).fetchall()
    assert rows == [("imapB", "Also From B"), ("imapA", "From A"), ("imapB", "From B")]


def test_refresh_imap_sequential_issues_one_search_across_all_sources_in_one_folder(tmp_path):
    # Regression guard: _refresh_imap_sequential is the actual multi-source
    # entry point refresh_all uses — it must batch every source into one
    # refresh_imap_sources() call, not call it once per source (which would
    # each be a "group of one" and defeat the whole consolidation).
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    sources = [
        Source(key=f"nl{i}", type="imap", title=f"N{i}", folder="Test", query="")
        for i in range(3)
    ]
    search_calls = []

    def search_fn(client, folder, **kw):
        search_calls.append(folder)
        return []

    class FakeClient:
        def logout(self):
            pass

    reports = _refresh_imap_sequential(
        conn, sources, lambda: FakeClient(), search_fn=search_fn, fetch_fn=lambda c, m: b""
    )

    assert len(search_calls) == 1
    assert len(reports) == 3


def test_refresh_imap_sources_issues_one_search_per_folder_not_per_source(tmp_path):
    # The actual point of the consolidation: N newsletter sources sharing
    # the default folder must produce exactly one SEARCH, not one each.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    sources = [
        Source(key=f"nl{i}", type="imap", title=f"N{i}", folder="Test", query="")
        for i in range(4)
    ]
    search_calls = []

    def search_fn(client, folder, **kw):
        search_calls.append(folder)
        return []

    reports = refresh_imap_sources(
        conn, sources, imap_client=object(), search_fn=search_fn, fetch_fn=lambda c, m: b""
    )

    assert len(search_calls) == 1
    assert len(reports) == 4
    assert all(r["status"] == "ok" for r in reports.values())


def test_refresh_imap_sources_fetches_each_message_exactly_once_even_when_it_matches_two_sources(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    # Both sources have an empty query (matches everything), so the same
    # message legitimately belongs to both — must still be FETCHed once.
    sources = [
        Source(key="nl-a", type="imap", title="A", folder="Test", query=""),
        Source(key="nl-b", type="imap", title="B", folder="Test", query=""),
    ]
    fetch_calls = []

    def fetch_fn(client, message_id):
        fetch_calls.append(message_id)
        return make_imap_message("m1", "Shared newsletter")

    reports = refresh_imap_sources(
        conn, sources, imap_client=object(),
        search_fn=lambda c, f, **kw: ["1"], fetch_fn=fetch_fn,
    )

    assert fetch_calls == ["1"]  # exactly one FETCH, not two
    assert reports["nl-a"]["new"] == 1
    assert reports["nl-b"]["new"] == 1
    row = conn.execute("SELECT COUNT(*) FROM articles").fetchone()
    assert row[0] == 2  # one row per source, both from the single fetch


def test_refresh_imap_sources_routes_by_from_query_locally(tmp_path):
    # Two sources sharing a folder, distinguished only by from: — replaces
    # what used to be a server-side SEARCH FROM filter with local matching
    # after one shared SEARCH+FETCH pass.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    sources = [
        Source(key="from-a", type="imap", title="A", folder="Test", query="from:sender-a@example.com"),
        Source(key="from-b", type="imap", title="B", folder="Test", query="from:sender-b@example.com"),
    ]

    def make(mid, subject, sender):
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Message-Id"] = f"<{mid}@newsletter.example.com>"
        msg["Subject"] = subject
        msg["From"] = sender
        msg["Date"] = "Tue, 11 Aug 2026 05:30:24 +0000"
        msg.set_content("<p>body</p>", subtype="html")
        return bytes(msg)

    raw_messages = {
        "1": make("m1", "From A", "Sender A <sender-a@example.com>"),
        "2": make("m2", "From B", "Sender B <sender-b@example.com>"),
    }

    reports = refresh_imap_sources(
        conn, sources, imap_client=object(),
        search_fn=lambda c, f, **kw: list(raw_messages.keys()),
        fetch_fn=lambda c, mid: raw_messages[mid],
    )

    assert reports["from-a"]["new"] == 1
    assert reports["from-b"]["new"] == 1
    rows = dict(conn.execute(
        "SELECT s.key, a.title FROM articles a JOIN sources s ON s.id = a.source_id"
    ).fetchall())
    assert rows == {"from-a": "From A", "from-b": "From B"}


def test_refresh_imap_sources_caps_lookback_at_seven_days(tmp_path):
    from datetime import UTC, datetime

    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    # No last_fetched_at yet — would otherwise default to a 30-day window.
    source = Source(key="brand-new", type="imap", title="New", folder="Test", query="")
    sinces_seen = []

    def search_fn(client, folder, since=None, **kw):
        sinces_seen.append(since)
        return []

    refresh_imap_sources(conn, [source], imap_client=object(), search_fn=search_fn, fetch_fn=lambda c, m: b"")

    age_days = (datetime.now(UTC) - sinces_seen[0]).days
    assert age_days <= 7


def test_refresh_imap_sources_new_source_backfill_not_truncated_by_synced_sibling(tmp_path):
    # The min-not-max fix: a source that's already caught up (recent,
    # narrow `since`) sharing a folder with a brand-new source (wide
    # default `since`, capped at 7 days) must not narrow the shared SEARCH
    # down to the synced sibling's tighter window — that would silently
    # and permanently skip the new source's intended backfill.
    from datetime import UTC, datetime, timedelta

    conn = connect(tmp_path / "reader.db")
    init_schema(conn)

    synced = Source(key="synced", type="imap", title="Synced", folder="Test", query="")
    new = Source(key="brand-new", type="imap", title="New", folder="Test", query="")

    # Prime "synced" with a very recent last_fetched_at, as if it just ran.
    conn.execute(
        "INSERT INTO sources (key, type, title, folder, last_fetched_at) VALUES (?, 'imap', 'Synced', 'Test', ?)",
        ("synced", datetime.now(UTC).isoformat()),
    )
    conn.commit()

    sinces_seen = []

    def search_fn(client, folder, since=None, **kw):
        sinces_seen.append(since)
        return []

    refresh_imap_sources(
        conn, [synced, new], imap_client=object(), search_fn=search_fn, fetch_fn=lambda c, m: b""
    )

    # Should reach back close to the 7-day cap for the new source, not be
    # truncated to ~1 day (synced's own since, after overlap subtraction).
    assert sinces_seen[0] < datetime.now(UTC) - timedelta(days=5)
