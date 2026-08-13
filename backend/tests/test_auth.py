"""Tests for the app-layer login that replaced Apache Basic Auth
(docs/WORKLOG.md, 2026-08-13 cont.): password check, session cookie
issuance/verification, and that AuthMiddleware actually gates protected
routes while leaving /api/login and /api/logout reachable.
"""
import bcrypt
import pytest
from starlette.testclient import TestClient

from app import auth, settings
from app.db import connect, init_schema
from app.main import create_app

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "AUTH_PASSWORD_HASH", bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    )
    monkeypatch.setattr(settings, "SESSION_SECRET", "test-secret-not-for-production")

    db_path = tmp_path / "reader.db"
    init_schema(connect(db_path))

    app = create_app(
        db_path=db_path,
        config_path=tmp_path / "feeds.yaml",
        require_auth=True,
    )
    # https:// base_url, not the default http://testserver — the session
    # cookie is Secure, so an httpx/TestClient cookie jar silently drops it
    # on a plain-http request, which would make every "log in, then hit a
    # protected route" test fail for a reason unrelated to auth logic.
    return TestClient(app, base_url="https://testserver")


def test_login_with_correct_password_sets_session_cookie(client):
    resp = client.post("/api/login", json={"password": PASSWORD})
    assert resp.status_code == 200
    assert auth.SESSION_COOKIE_NAME in resp.cookies


def test_login_with_wrong_password_is_rejected_without_a_cookie(client):
    resp = client.post("/api/login", json={"password": "wrong"})
    assert resp.status_code == 401
    assert auth.SESSION_COOKIE_NAME not in resp.cookies


def test_protected_route_without_a_cookie_is_401(client):
    resp = client.get("/api/sources")
    assert resp.status_code == 401


def test_protected_route_with_a_valid_session_cookie_passes_through(client):
    client.post("/api/login", json={"password": PASSWORD})
    resp = client.get("/api/sources")
    assert resp.status_code == 200


def test_expired_session_token_is_rejected(client):
    payload = "1"  # epoch second 1 — long expired
    token = f"{payload}.{auth._sign(payload.encode('ascii'))}"
    client.cookies.set(auth.SESSION_COOKIE_NAME, token)
    resp = client.get("/api/sources")
    assert resp.status_code == 401


def test_tampered_session_token_is_rejected(client):
    client.post("/api/login", json={"password": PASSWORD})
    real_token = client.cookies.get(auth.SESSION_COOKIE_NAME)
    payload, _signature = real_token.split(".", 1)
    client.cookies.set(auth.SESSION_COOKIE_NAME, f"{payload}.forged-signature")
    resp = client.get("/api/sources")
    assert resp.status_code == 401


def test_login_and_logout_are_reachable_with_no_session_at_all(client):
    # Login already covered above; logout must be a harmless no-op when
    # there's nothing to clear, not a 401 that would make it unreachable
    # exactly when a stale/invalid session needs clearing.
    resp = client.post("/api/logout")
    assert resp.status_code == 200


def test_logout_clears_the_session_cookie(client):
    client.post("/api/login", json={"password": PASSWORD})
    client.post("/api/logout")
    resp = client.get("/api/sources")
    assert resp.status_code == 401


def test_fully_unconfigured_auth_is_permissive(client, monkeypatch):
    # Both env vars unset is the default for local/LAN use, matching every
    # other optional credential in this app (IMAP_HOST, READONLY_CONFIG) —
    # a login was never required before this feature existed, and it
    # shouldn't become required just by not setting anything.
    monkeypatch.setattr(settings, "AUTH_PASSWORD_HASH", None)
    monkeypatch.setattr(settings, "SESSION_SECRET", None)
    resp = client.get("/api/sources")
    assert resp.status_code == 200


def test_partially_configured_auth_fails_closed(client, monkeypatch):
    # Exactly one of the two set looks like a deploy mistake, not an
    # intentional "no auth" choice — falling back to permissive here would
    # be the wrong failure mode for a typo in the one thing standing in
    # for Basic Auth on the VM.
    client.post("/api/login", json={"password": PASSWORD})
    monkeypatch.setattr(settings, "SESSION_SECRET", None)
    resp = client.get("/api/sources")
    assert resp.status_code == 401
