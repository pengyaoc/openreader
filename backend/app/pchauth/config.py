"""Config loading for pchauth. One AuthConfig per service; load_config
reads it from that service's env vars under a caller-chosen prefix (e.g.
READER_, SUMMRA_) so multiple services can vendor this file without
colliding on env var names.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

_VALID_MODES = {"off", "optional", "required"}


class ConfigError(Exception):
    """Raised for a missing or invalid config value. The message names the
    exact env var at fault, since this fires at process startup where the
    only feedback is what ends up in a systemd journal."""


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "required"
    header_name: str = "X-Remote-Email"
    allowed_emails: frozenset[str] = frozenset()


def load_config(env: Mapping[str, str], *, prefix: str) -> AuthConfig:
    def get(name: str, default: str | None = None) -> str | None:
        return env.get(f"{prefix}_{name}", default)

    mode = get("AUTH_MODE", "required")
    if mode not in _VALID_MODES:
        raise ConfigError(f"{prefix}_AUTH_MODE={mode!r} is not one of {sorted(_VALID_MODES)}")

    header_name = get("AUTH_HEADER_NAME", "X-Remote-Email")

    raw_emails = get("ALLOWED_EMAILS", "") or ""
    allowed_emails = frozenset(
        email.strip().lower() for email in raw_emails.split(",") if email.strip()
    )

    if mode != "off" and not allowed_emails:
        raise ConfigError(
            f"{prefix}_ALLOWED_EMAILS is empty but {prefix}_AUTH_MODE={mode!r} — "
            "an empty allowlist in a non-off mode locks everyone out, which is "
            "almost always a deploy mistake; set the allowlist or use mode=off"
        )

    return AuthConfig(mode=mode, header_name=header_name, allowed_emails=allowed_emails)
