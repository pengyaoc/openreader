"""pchauth: shared trusted-header login for pengyaochen.com services.

Apache's mod_auth_openidc is the actual OIDC client (see
docs/superpowers/specs/2026-09-05-consolidated-login-design.md); this
library only consumes the X-Remote-Email header it injects. The
self_oidc identity source (standalone, no-gateway deployments) is
deferred — see each consuming repo's worklog, 2026-09-05.
"""
from .config import AuthConfig, ConfigError, load_config
from .core import Identity, is_allowed

__all__ = ["AuthConfig", "ConfigError", "load_config", "Identity", "is_allowed"]
