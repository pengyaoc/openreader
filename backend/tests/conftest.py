"""Shared fixtures and helpers.

Mostly account plumbing: every test DB gets user 1 for free from
init_schema's ensure_default_user, but anything exercising the *point* of
multi-user needs a second account, and anything exercising login needs one
with a known password.
"""
import sqlite3
from datetime import UTC, datetime

import bcrypt
import pytest


def hash_password(password: str) -> str:
    # Lowest legal cost factor: these hashes are created and verified many
    # times across the suite, and the default cost adds ~200ms per call.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("ascii")


def seed_user(conn: sqlite3.Connection, username: str, password: str | None = None) -> int:
    """Creates an account and returns its id. Mirrors what
    `python -m app.useradm add` does, minus the prompting."""
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, hash_password(password) if password else "", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def read_state(conn: sqlite3.Connection, user_id: int, guid: str) -> tuple[int, str | None]:
    """(is_read, read_at) for one user and one article, resolving a missing
    row to the unread default — the same COALESCE the store does, so tests
    can assert on state without caring whether a row exists yet."""
    row = conn.execute(
        """SELECT COALESCE(st.is_read, 0), st.read_at
           FROM articles a
           LEFT JOIN article_states st ON st.article_id = a.id AND st.user_id = ?
           WHERE a.guid = ?""",
        (user_id, guid),
    ).fetchone()
    return (row[0], row[1]) if row else (0, None)


@pytest.fixture
def two_users(tmp_path):
    """A DB with two accounts, returned as (conn, alice_id, bob_id)."""
    from app.db import connect, init_schema

    conn = connect(tmp_path / "reader.db")
    init_schema(conn)
    # init_schema already seeded user 1; rename it rather than adding a
    # third, so ids stay 1 and 2 the way a real deployment's do.
    conn.execute("UPDATE users SET username = 'alice' WHERE id = 1")
    conn.commit()
    return conn, 1, seed_user(conn, "bob")
