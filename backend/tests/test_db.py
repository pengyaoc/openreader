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
    assert {"sources", "articles", "users", "article_states"} <= tables
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


def _legacy_single_user_db(path):
    """A pre-2026-08-31 database: read/starred as columns on articles, no
    users table, and the three indexes that referenced those columns. This
    is the shape the live deployment's DB was in."""
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE sources (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL, title TEXT NOT NULL, folder TEXT NOT NULL, url TEXT);
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            guid TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            published_at TEXT,
            content_hash TEXT,
            origin TEXT NOT NULL DEFAULT 'feed',
            is_read INTEGER NOT NULL DEFAULT 0,
            read_at TEXT,
            is_starred INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source_id, guid)
        );
        CREATE INDEX idx_articles_unread ON articles(is_read, published_at DESC);
        CREATE INDEX idx_articles_starred ON articles(is_starred, published_at DESC);
        CREATE INDEX idx_articles_src_unread ON articles(source_id, is_read);
        INSERT INTO sources (key, type, title, folder) VALUES ('s1', 'rss', 'S', 'F');
        INSERT INTO articles (source_id, guid, url, title, is_read, read_at, is_starred) VALUES
            (1, 'read',        'https://x/1', 'Read',        1, '2026-08-01T00:00:00+00:00', 0),
            (1, 'starred',     'https://x/2', 'Starred',     0, NULL,                        1),
            (1, 'read+star',   'https://x/3', 'Both',        1, '2026-08-02T00:00:00+00:00', 1),
            (1, 'untouched',   'https://x/4', 'Untouched',   0, NULL,                        0);
        """
    )
    conn.commit()
    return conn


def _states(conn, user_id):
    return {
        guid: (is_read, read_at, is_starred)
        for guid, is_read, read_at, is_starred in conn.execute(
            """SELECT a.guid, st.is_read, st.read_at, st.is_starred
               FROM article_states st JOIN articles a ON a.id = st.article_id
               WHERE st.user_id = ?""",
            (user_id,),
        )
    }


def test_init_schema_migrates_single_user_read_state_to_the_first_account(tmp_path):
    conn = _legacy_single_user_db(tmp_path / "reader.db")

    init_schema(conn)

    # Only touched articles get a row; "untouched" stays absent, which is
    # what makes it read as unread for user 1 and for everyone added later.
    assert _states(conn, 1) == {
        "read": (1, "2026-08-01T00:00:00+00:00", 0),
        "starred": (0, None, 1),
        "read+star": (1, "2026-08-02T00:00:00+00:00", 1),
    }

    # A second account added afterwards inherits nothing: every existing
    # article is unread and unstarred for them.
    conn.execute(
        "INSERT INTO users (username, created_at) VALUES ('second', '2026-08-31T00:00:00+00:00')"
    )
    conn.commit()
    assert _states(conn, 2) == {}

    columns = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert not columns & {"is_read", "read_at", "is_starred"}
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert not indexes & {
        "idx_articles_unread", "idx_articles_starred", "idx_articles_src_unread"
    }

    # Every real restart re-runs this against the already-migrated DB.
    init_schema(conn)
    assert len(_states(conn, 1)) == 3


def test_migration_resumes_cleanly_when_interrupted_after_the_backfill(tmp_path):
    # The failure window is between writing article_states and dropping the
    # columns: a crash there leaves both copies of the state present, and
    # the next startup re-runs the backfill over rows that already exist.
    from app.db import SCHEMA, _migrate_per_user_state, ensure_default_user

    conn = _legacy_single_user_db(tmp_path / "reader.db")
    # Replay init_schema up to, but not including, the migration: the
    # CREATE TABLE IF NOT EXISTS for articles no-ops against the legacy
    # table, so this leaves exactly the pre-backfill state.
    conn.executescript(SCHEMA)
    ensure_default_user(conn)
    conn.execute(
        """INSERT INTO article_states (user_id, article_id, is_read, read_at, is_starred)
           SELECT 1, id, is_read, read_at, is_starred FROM articles
           WHERE is_read = 1 OR is_starred = 1"""
    )
    conn.commit()

    _migrate_per_user_state(conn)  # the resumed run

    assert _states(conn, 1) == {
        "read": (1, "2026-08-01T00:00:00+00:00", 0),
        "starred": (0, None, 1),
        "read+star": (1, "2026-08-02T00:00:00+00:00", 1),
    }
    columns = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert not columns & {"is_read", "read_at", "is_starred"}


def test_ensure_default_user_never_overwrites_an_existing_account(tmp_path):
    from app.db import ensure_default_user

    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    conn.execute("UPDATE users SET username = 'pengyao', password_hash = 'kept' WHERE id = 1")
    conn.commit()

    ensure_default_user(conn)
    init_schema(conn)

    assert conn.execute("SELECT username, password_hash FROM users").fetchall() == [
        ("pengyao", "kept")
    ]


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


def test_connect_sets_read_performance_pragmas(tmp_path):
    conn = connect(tmp_path / "reader.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY


def test_init_schema_creates_read_path_indexes(tmp_path):
    # These back the article-list and sidebar unread-count queries
    # (app/store.py) — a missing one silently degrades to a full table scan
    # rather than erroring, so assert their presence explicitly.
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert {
        "idx_articles_source_pub",
        "idx_articles_pub",
        "idx_sources_folder",
        "idx_article_states_starred",
        "idx_article_states_article",
    } <= indexes
    # The read/starred indexes went with the columns they indexed
    # (2026-08-31). Asserted absent rather than just dropped from the set
    # above, because they'd have to be dropped from a live DB too — and a
    # surviving one blocks ALTER TABLE DROP COLUMN outright.
    assert not indexes & {
        "idx_articles_unread",
        "idx_articles_starred",
        "idx_articles_src_unread",
    }
