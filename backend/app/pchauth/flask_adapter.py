"""Flask adapter for pchauth. login_required's error shape
({'error': 'Authentication required'}, 401) matches
summra/backend/auth_utils.py exactly, so replacing that decorator with
this one needs no call-site changes.
"""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import Flask, g, jsonify, request

from .config import AuthConfig
from .core import Identity, is_allowed
from .trusted_header import identity_from_headers

_OFF_MODE_IDENTITY = Identity(email="")


def login_required(view_func: Callable) -> Callable:
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if g.get("user_id") is None:
            return jsonify({"error": "Authentication required"}), 401
        return view_func(*args, **kwargs)
    return wrapped


def init_pchauth(app: Flask, config: AuthConfig, upsert_user: Callable[[Identity], int | None]) -> None:
    @app.before_request
    def _resolve_user():
        if config.mode == "off":
            g.user_id = upsert_user(_OFF_MODE_IDENTITY)
            return None

        identity = identity_from_headers(request.headers, config)
        if identity is None:
            g.user_id = None
            if config.mode == "required":
                return jsonify({"error": "not authenticated"}), 401
            return None

        if not is_allowed(identity.email, config):
            return jsonify({"error": "not on the allowlist"}), 403

        g.user_id = upsert_user(identity)
        return None
