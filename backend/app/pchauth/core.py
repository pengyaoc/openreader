"""Framework-agnostic identity types and the allowlist check. See
docs/superpowers/specs/2026-09-05-consolidated-login-design.md — this
round's only identity source is trusted_header (pchauth/trusted_header.py);
Identity.subject/name are reserved for the deferred self_oidc source and
are always None here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import AuthConfig


@dataclass(frozen=True)
class Identity:
    email: str
    subject: str | None = None
    name: str | None = None


def is_allowed(email: str, config: AuthConfig) -> bool:
    """Fails closed: an empty allowlist means nobody is allowed. load_config
    already refuses to start with an empty allowlist in a non-off mode, so
    reaching this with one is a defense-in-depth check, not the primary
    guard."""
    return email.strip().lower() in config.allowed_emails
