"""Tests for app.api._common's current_user_id/require_user_id split
(consolidated login, 2026-09-05). current_user_id may now return None for
an anonymous request under optional mode; require_user_id raises
AnonymousUserError instead, so a write handler that forgets to check the
return value fails loudly rather than silently writing under the wrong
identity.
"""
import pytest
from starlette.requests import Request

from app.api._common import AnonymousUserError, current_user_id, require_user_id
from app.db import DEFAULT_USER_ID


def _fake_request(user_id):
    scope = {"type": "http", "state": {"user_id": user_id}}
    return Request(scope)


def test_current_user_id_returns_none_when_anonymous():
    assert current_user_id(_fake_request(None)) is None


def test_current_user_id_returns_int_when_authenticated():
    assert current_user_id(_fake_request(42)) == 42


def test_current_user_id_falls_back_to_default_when_state_missing():
    # off mode / no middleware in the stack: scope has no "user_id" key at
    # all, distinct from a middleware explicitly setting it to None.
    scope = {"type": "http", "state": {}}
    request = Request(scope)
    assert current_user_id(request) == DEFAULT_USER_ID


def test_require_user_id_returns_int_when_authenticated():
    assert require_user_id(_fake_request(42)) == 42


def test_require_user_id_raises_when_anonymous():
    with pytest.raises(AnonymousUserError):
        require_user_id(_fake_request(None))


def test_require_user_id_returns_default_when_state_missing():
    # off mode: require_user_id should succeed too, same as current_user_id.
    scope = {"type": "http", "state": {}}
    request = Request(scope)
    assert require_user_id(request) == DEFAULT_USER_ID
