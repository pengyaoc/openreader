"""The trusted_header identity source: reads the email Apache's
mod_auth_openidc already verified and injected as a request header. No
network calls, no crypto — trust was established by Apache before this
function ever runs. Safe only because the app process is unreachable
except through Apache (loopback-only bind) — see the spec's Architecture
section, "Header spoofing," for the full trust-boundary argument.
"""
from __future__ import annotations

from typing import Mapping

from .config import AuthConfig
from .core import Identity


def identity_from_headers(headers: Mapping[str, str], config: AuthConfig) -> Identity | None:
    target = config.header_name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            email = value.strip().lower()
            return Identity(email=email) if email else None
    return None
