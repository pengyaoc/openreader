"""Small pieces shared by two or more API route modules — not a general
dumping ground, just the handful of things that would otherwise be
copy-pasted per route file."""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import settings
from app.config import Config, to_yaml


def readonly_response() -> JSONResponse | None:
    """Returns a 403 if config writes are locked on this deployment, else
    None — call at the top of any config-mutating endpoint before touching
    disk."""
    if settings.READONLY_CONFIG:
        return JSONResponse(
            {"error": "config is read-only on this deployment"}, status_code=403
        )
    return None


def save_config(request: Request, new_config: Config) -> None:
    """Writes a new Config to feeds.yaml and swaps it into app state — the
    two steps every config-mutating endpoint performs after validating its
    change, always together."""
    request.app.state.config_path.write_text(to_yaml(new_config))
    request.app.state.config = new_config
