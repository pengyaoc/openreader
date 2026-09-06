"""Tests for the consolidated login (2026-09-05): identity now comes from
Apache's mod_auth_openidc gateway via an X-Remote-Email header, read
through the shared pchauth library, not an app-layer password/cookie.
See docs/WORKLOG.md and pchauth's spec,
docs/superpowers/specs/2026-09-05-consolidated-login-design.md.

The bcrypt/session-cookie functions in app/auth.py are a documented
rollback path, kept but unused — this file tests the code path that's
actually live.
"""
import pytest
from starlette.testclient import TestClient

from app.db import connect, init_schema
from app.main import create_app
from tests.conftest import seed_user


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "reader.db"
    conn = connect(path)
    init_schema(conn)  # seeds account 1, username 'reader'
    conn.execute("UPDATE users SET username = 'alice', email = 'alice@example.com' WHERE id = 1")
    conn.commit()
    bob_id = seed_user(conn, "bob")
    conn.execute("UPDATE users SET email = 'bob@example.com' WHERE id = ?", (bob_id,))
    conn.commit()
    return path


@pytest.fixture()
def client(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("READER_AUTH_MODE", "required")
    monkeypatch.setenv("READER_ALLOWED_EMAILS", "alice@example.com,bob@example.com")
    app = create_app(
        db_path=db_path,
        config_path=tmp_path / "feeds.yaml",
        require_auth=True,
    )
    return TestClient(app)


def test_protected_route_without_a_header_is_401(client):
    assert client.get("/api/sources").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_protected_route_with_a_valid_header_passes_through(client):
    resp = client.get("/api/sources", headers={"X-Remote-Email": "alice@example.com"})
    assert resp.status_code == 200


def test_header_with_an_email_not_on_the_allowlist_is_403(client):
    resp = client.get("/api/sources", headers={"X-Remote-Email": "stranger@example.com"})
    assert resp.status_code == 403


def test_the_header_identifies_which_account_is_making_the_request(client):
    resp = client.get("/api/me", headers={"X-Remote-Email": "bob@example.com"})
    assert resp.json()["username"] == "bob"

    resp = client.get("/api/me", headers={"X-Remote-Email": "alice@example.com"})
    assert resp.json()["username"] == "alice"


def test_header_lookup_is_case_insensitive_on_the_email(client):
    resp = client.get("/api/me", headers={"X-Remote-Email": "Alice@Example.com"})
    assert resp.json()["username"] == "alice"


def test_there_is_no_login_or_logout_route_any_more(client):
    # Neither route exists; PchauthMiddleware gates the whole /api/ prefix
    # ahead of routing in `required` mode, so a signed-in request is what
    # actually proves the route itself is gone (404), not a bare request
    # (which 401s before routing ever runs).
    authed = {"X-Remote-Email": "alice@example.com"}
    assert client.post("/api/login", headers=authed).status_code == 404
    assert client.post("/api/logout", headers=authed).status_code == 404


def test_unconfigured_auth_is_permissive_and_acts_as_the_first_account(tmp_path, monkeypatch):
    # READER_AUTH_MODE unset is the default for local/LAN use, matching
    # every other optional credential in this app (IMAP_HOST,
    # READONLY_CONFIG) — a login was never required before this feature
    # existed, and it shouldn't become required just by not setting
    # anything. Requests still need an identity, and it's account 1 — with
    # no DB write at all for the off-mode sentinel identity.
    monkeypatch.delenv("READER_AUTH_MODE", raising=False)
    monkeypatch.delenv("READER_ALLOWED_EMAILS", raising=False)

    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute("UPDATE users SET username = 'alice' WHERE id = 1")
    conn.commit()

    app = create_app(db_path=db_path, config_path=tmp_path / "feeds.yaml", require_auth=True)
    client = TestClient(app)

    assert client.get("/api/sources").status_code == 200
    assert client.get("/api/me").json() == {
        "id": 1, "username": "alice", "auth_enabled": False
    }


def test_optional_mode_allows_anonymous_reads_but_401s_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("READER_AUTH_MODE", "optional")
    monkeypatch.setenv("READER_ALLOWED_EMAILS", "alice@example.com")

    db_path = tmp_path / "reader.db"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute("UPDATE users SET username = 'alice', email = 'alice@example.com' WHERE id = 1")
    conn.commit()

    app = create_app(db_path=db_path, config_path=tmp_path / "feeds.yaml", require_auth=True)
    client = TestClient(app)

    anon_me = client.get("/api/me")
    assert anon_me.status_code == 200
    assert anon_me.json() == {"id": None, "username": None, "auth_enabled": True}

    assert client.get("/api/sources").status_code == 200

    authed = client.get("/api/me", headers={"X-Remote-Email": "alice@example.com"})
    assert authed.json()["username"] == "alice"
