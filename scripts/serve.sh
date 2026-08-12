#!/usr/bin/env bash
# Production-style run: rebuild the frontend, then let the backend serve it
# directly on one port (0.0.0.0:8787 — reachable from other devices on your
# LAN, e.g. a phone). Exists because "backend serves a stale frontend/dist"
# is a real, silent failure mode: the build step is easy to forget after
# editing frontend/src, and nothing errors — you just keep seeing old CSS/JS
# (see docs/WORKLOG.md, 2026-08-12, for the actual bug this caused).
#
# Usage: ./scripts/serve.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Building frontend..."
(cd frontend && npm run build)

echo "==> Starting backend on 0.0.0.0:8787 (serving frontend/dist)..."
cd backend
exec uv run uvicorn app.asgi:app --host 0.0.0.0 --port 8787
