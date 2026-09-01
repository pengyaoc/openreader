"""Tests for the app-layer login that replaced Apache Basic Auth
(docs/WORKLOG.md, 2026-08-13 cont.) and became multi-account on
2026-08-31: password check against the users table, session cookie
issuance/verification including the account id it carries, and that
AuthMiddleware actually gates protected routes while leaving /api/login
and /api/logout reachable.
"""
import pytest
from starlette.testclient import TestClient

from app import auth, settings
from app.db import connect, init_schema
from app.main import create_app
from tests.conftest import hash_password, seed_user

PASSWORD = "correct horse battery staple"
BOB_PASSWORD = "a different one entirely"


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setattr(settings, "SESSION_SECRET", "test-secret-not-for-production")

    path = tmp_path / "reader.db"
    conn = connect(path)
    init_schema(conn)  # seeds account 1 with AUTH_PASSWORD_HASH above
    conn.execute("UPDATE users SET username = 'alice' WHERE id = 1")
    conn.commit()
    seed_user(conn, "bob", BOB_PASSWORD)
    return path


@pytest.fixture()
def client(db_path, tmp_path):
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


def login(client, username, password):
    return client.post("/api/login", json={"username": username, "password": password})


def test_login_with_correct_credentials_sets_session_cookie(client):
    resp = login(client, "alice", PASSWORD)
    assert resp.status_code == 200
    assert resp.json()["username"] == "alice"
    assert auth.SESSION_COOKIE_NAME in resp.cookies


def test_login_with_wrong_password_is_rejected_without_a_cookie(client):
    resp = login(client, "alice", "wrong")
    assert resp.status_code == 401
    assert auth.SESSION_COOKIE_NAME not in resp.cookies


def test_login_with_an_unknown_username_is_rejected_the_same_way(client):
    # Identical status and message to a wrong password: which half was
    # wrong is exactly what an attacker wants to learn.
    unknown = login(client, "nobody", PASSWORD)
    wrong_password = login(client, "alice", "wrong")
    assert unknown.status_code == 401
    assert unknown.json() == wrong_password.json()
    assert auth.SESSION_COOKIE_NAME not in unknown.cookies


def test_one_accounts_password_does_not_work_for_another(client):
    assert login(client, "bob", PASSWORD).status_code == 401
    assert login(client, "bob", BOB_PASSWORD).status_code == 200


def test_protected_route_without_a_cookie_is_401(client):
    assert client.get("/api/sources").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_protected_route_with_a_valid_session_cookie_passes_through(client):
    login(client, "alice", PASSWORD)
    assert client.get("/api/sources").status_code == 200


def test_the_session_identifies_which_account_logged_in(client):
    login(client, "bob", BOB_PASSWORD)
    assert client.get("/api/me").json() == {"id": 2, "username": "bob", "auth_enabled": True}

    login(client, "alice", PASSWORD)
    assert client.get("/api/me").json()["username"] == "alice"


def test_expired_session_token_is_rejected(client):
    payload = "1:1"  # account 1, epoch second 1 — long expired
    client.cookies.set(auth.SESSION_COOKIE_NAME, f"{payload}.{auth._sign(payload.encode())}")
    assert client.get("/api/sources").status_code == 401


def test_tampered_session_token_is_rejected(client):
    login(client, "alice", PASSWORD)
    payload, _signature = client.cookies.get(auth.SESSION_COOKIE_NAME).split(".", 1)
    client.cookies.set(auth.SESSION_COOKIE_NAME, f"{payload}.forged-signature")
    assert client.get("/api/sources").status_code == 401


def test_swapping_the_account_id_in_a_token_invalidates_it(client):
    # The whole basis of account separation: the HMAC covers the id, so
    # alice cannot edit her own valid cookie into bob's session.
    login(client, "alice", PASSWORD)
    token = client.cookies.get(auth.SESSION_COOKIE_NAME)
    payload, signature = token.split(".", 1)
    _user_id, expires_at = payload.split(":", 1)
    client.cookies.set(auth.SESSION_COOKIE_NAME, f"2:{expires_at}.{signature}")
    assert client.get("/api/sources").status_code == 401


def test_pre_multi_user_session_tokens_are_rejected(client):
    # Old format was "<expiry>.<sig>" with no account id. Such a cookie has
    # no identity to attribute, and honoring it as the default account
    # would make account separation bypassable by holding a stale cookie —
    # so it's rejected and the holder logs in once more.
    import time

    payload = str(int(time.time()) + 3600)
    client.cookies.set(auth.SESSION_COOKIE_NAME, f"{payload}.{auth._sign(payload.encode())}")
    assert client.get("/api/sources").status_code == 401


def test_a_session_for_a_deleted_account_is_rejected_and_cleared(client, db_path):
    login(client, "bob", BOB_PASSWORD)
    conn = connect(db_path)
    conn.execute("DELETE FROM users WHERE username = 'bob'")
    conn.commit()

    resp = client.get("/api/me")
    assert resp.status_code == 401
    assert client.cookies.get(auth.SESSION_COOKIE_NAME) in (None, "")


def test_login_and_logout_are_reachable_with_no_session_at_all(client):
    # Login already covered above; logout must be a harmless no-op when
    # there's nothing to clear, not a 401 that would make it unreachable
    # exactly when a stale/invalid session needs clearing.
    assert client.post("/api/logout").status_code == 200


def test_logout_clears_the_session_cookie(client):
    login(client, "alice", PASSWORD)
    client.post("/api/logout")
    assert client.get("/api/sources").status_code == 401


def test_unconfigured_auth_is_permissive_and_acts_as_the_first_account(client, monkeypatch):
    # No session secret is the default for local/LAN use, matching every
    # other optional credential in this app (IMAP_HOST, READONLY_CONFIG) —
    # a login was never required before this feature existed, and it
    # shouldn't become required just by not setting anything. Requests
    # still need an identity, and it's account 1.
    monkeypatch.setattr(settings, "SESSION_SECRET", None)
    assert client.get("/api/sources").status_code == 200
    assert client.get("/api/me").json() == {
        "id": 1, "username": "alice", "auth_enabled": False
    }


def test_a_leftover_password_hash_alone_does_not_require_a_login(client, monkeypatch):
    # AUTH_PASSWORD_HASH is a first-run seed, not a live credential: once
    # it's been consumed, whether it's still in the env file says nothing
    # about whether this deployment wants a login. Gating on it would mean
    # tidying the dead variable away silently locked everyone out — and the
    # reverse pairing (secret set, hash gone) is the *normal* steady state.
    # main.py warns at startup when a multi-account DB has no secret; this
    # asserts the middleware itself keys off the secret alone.
    monkeypatch.setattr(settings, "SESSION_SECRET", None)
    assert client.get("/api/sources").status_code == 200
