"""Tests for app.db: schema creation and basic invariants."""
import sqlite3

from app.db import connect, init_schema


def test_init_schema_creates_expected_tables(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"sources", "articles"} <= tables
    assert "jobs" not in tables


def test_init_schema_drops_jobs_table_and_generation_columns_from_an_older_db(tmp_path):
    # Simulates a pre-2026-08-14 database (topic-generation era schema) —
    # init_schema must clean these up on next startup, not just skip
    # creating them on a fresh DB.
    conn = connect(tmp_path / "reader.db")
    conn.executescript(
        """
        CREATE TABLE sources (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL, title TEXT NOT NULL, folder TEXT NOT NULL, url TEXT);
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            guid TEXT NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT,
            title TEXT NOT NULL,
            author TEXT,
            published_at TEXT,
            fetched_at TEXT,
            excerpt TEXT,
            content_html TEXT,
            top_image_path TEXT,
            content_hash TEXT,
            matched_rule TEXT,
            origin TEXT NOT NULL DEFAULT 'feed',
            job_id INTEGER REFERENCES jobs(id),
            citations_json TEXT,
            hydrated_at TEXT,
            hydrate_failed_at TEXT,
            is_read INTEGER NOT NULL DEFAULT 0,
            read_at TEXT,
            is_starred INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source_id, guid)
        );
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, topic_key TEXT NOT NULL);
        """
    )
    conn.commit()

    init_schema(conn)

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "jobs" not in tables
    columns = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert "job_id" not in columns
    assert "citations_json" not in columns

    # Idempotent — running it again against the already-migrated DB must
    # not raise (this is exactly what happens on every real app restart).
    init_schema(conn)


def test_articles_guid_unique_per_source(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, type, title, folder) VALUES (?, ?, ?, ?)",
        ("src1", "rss", "Source 1", "Test"),
    )
    source_id = conn.execute("SELECT id FROM sources WHERE key = 'src1'").fetchone()[0]
    conn.execute(
        "INSERT INTO articles (source_id, guid, url, title, origin) "
        "VALUES (?, 'g1', 'https://x/1', 'Title', 'feed')",
        (source_id,),
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO articles (source_id, guid, url, title, origin) "
            "VALUES (?, 'g1', 'https://x/1-dup', 'Title dup', 'feed')",
            (source_id,),
        )
        conn.commit()
        assert False, "expected UNIQUE constraint violation"
    except sqlite3.IntegrityError:
        pass


def test_db_journal_mode_is_wal(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
