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
    assert {"sources", "articles", "jobs"} <= tables


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
