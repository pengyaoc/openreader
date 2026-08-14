#!/usr/bin/env bash
# Deploys OpenReader to wordpress-1-vm. Everything that needs Node (tsc,
# Vite) or a real test run happens here, on your laptop — never on the
# 1 GB VM, which also runs WordPress. The VM only ever receives finished
# artifacts (backend source + frontend/dist) and restarts its own service;
# it never runs `git pull`, `npm install`, or `npm run build` itself.
#
# Does NOT touch config/feeds.yaml or data/ on the VM — that's the VM's own
# live state (your real source list, the SQLite DB), not part of a deploy.
#
# One-time prerequisite (not done by this script): the VM needs the
# `openreader` service user, /opt/openreader, uv installed for that user,
# and the openreader.service systemd --user unit already set up, with
# `loginctl enable-linger openreader` so it survives an SSH logout. See the
# co-hosting plan (docs/ or ~/.claude/plans/) for that one-off setup,
# including the IMAP credential file — a secret this script never touches
# or transfers; it's written once, by hand, directly on the VM.
#
# Second one-time prerequisite, added 2026-08-14 for LLM generation/
# summarization (both spawn the `claude` CLI as a subprocess of the
# openreader service): Node.js installed under /opt/node (prebuilt tarball
# from nodejs.org, apt is unusable on this buster VM — see below), the
# `claude` CLI installed for the openreader user via
# `npm install -g @anthropic-ai/claude-code` with a user-local npm prefix
# (~/.npm-global), and a one-time interactive `claude` login as the
# openreader user to seed ~/.claude/.credentials.json against the
# Pro/Max subscription (OAuth, not an API key — see
# backend/app/generate/client.py's module docstring for why that must
# never change). None of this is scripted here: the OAuth login step is
# inherently interactive (opens a URL you approve from a browser), so
# there's nothing for this script to automate. See docs/WORKLOG.md,
# 2026-08-14, for the exact commands.
#
# The openreader.service unit itself also needed two changes for this to
# work at all (also not managed by this script — hand-edited on the VM,
# see docs/WORKLOG.md): its PATH didn't include /opt/node/bin or
# ~/.npm-global/bin (so `claude` wasn't found), and MemoryMax=300M — sized
# only for the FastAPI process — was smaller than a single `claude -p`
# call's own ~300MB peak RSS, so the very first summarize/generate call
# would've been OOM-killed by the cgroup. Now PATH includes both, and
# MemoryMax=700M.
#
# Usage: ./scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ZONE="us-west1-a"
PROJECT="pelagic-magpie-277922"
VM="wordpress-1-vm"
REMOTE_USER="openreader"
REMOTE_DIR="/opt/openreader"
REMOTE_STAGE="/tmp/openreader_deploy"

echo "==> Type-checking frontend..."
(cd frontend && npx tsc --noEmit -p tsconfig.app.json)

echo "==> Running backend tests..."
(cd backend && uv run pytest -q)

echo "==> Building frontend (VITE_BASE=/reader/)..."
(cd frontend && VITE_BASE=/reader/ npm run build)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Staging deploy payload..."
mkdir -p "$STAGE/backend" "$STAGE/frontend"
# app/ + the uv project files is everything the VM's venv needs — no tests,
# no .venv, no __pycache__.
rsync -a --exclude='__pycache__' --exclude='*.pyc' \
  backend/app backend/pyproject.toml backend/uv.lock backend/.python-version \
  "$STAGE/backend/"
rsync -a frontend/dist "$STAGE/frontend/"

echo "==> Uploading to $VM:$REMOTE_STAGE ..."
gcloud compute ssh --zone "$ZONE" --project "$PROJECT" "$VM" \
  --command "rm -rf $REMOTE_STAGE && mkdir -p $REMOTE_STAGE"
gcloud compute scp --zone "$ZONE" --project "$PROJECT" --recurse \
  "$STAGE"/backend "$STAGE"/frontend "$VM:$REMOTE_STAGE/"

echo "==> Installing on VM and restarting service..."
# cp, not rsync: the VM's apt is broken (buster was archived at EOL — see
# the co-hosting plan's accepted-risks section) and rsync isn't installed.
# Fixing apt was explicitly out of scope, so this avoids needing it at all.
gcloud compute ssh --zone "$ZONE" --project "$PROJECT" "$VM" --command "
  set -euo pipefail
  sudo rm -rf $REMOTE_DIR/backend
  sudo cp -r $REMOTE_STAGE/backend $REMOTE_DIR/backend
  sudo rm -rf $REMOTE_DIR/frontend/dist
  sudo mkdir -p $REMOTE_DIR/frontend
  sudo cp -r $REMOTE_STAGE/frontend/dist $REMOTE_DIR/frontend/dist
  sudo chown -R $REMOTE_USER:$REMOTE_USER $REMOTE_DIR
  rm -rf $REMOTE_STAGE
  sudo -u $REMOTE_USER bash -lc 'cd $REMOTE_DIR/backend && uv sync'
  # loginctl enable-linger $REMOTE_USER (done once during provisioning) keeps
  # /run/user/<uid> alive without a login session, which is what lets a
  # --user systemd unit be controlled from outside that user's own session.
  uid=\$(id -u $REMOTE_USER)
  sudo -u $REMOTE_USER XDG_RUNTIME_DIR=/run/user/\$uid systemctl --user restart openreader
  sudo -u $REMOTE_USER XDG_RUNTIME_DIR=/run/user/\$uid systemctl --user is-active openreader
"

echo "==> Verifying..."
sleep 3
# /reader/ serves the SPA shell unauthenticated by design (2026-08-13 cont.
# — app-layer login replaced Apache Basic Auth; the login screen itself has
# to load before anyone's authenticated) — a bare request should get 200.
# The API underneath is still gated (AuthMiddleware); this script doesn't
# carry a session, so it can't check past that. Log in via the browser to
# confirm the app itself loaded.
curl -s -o /dev/null -w "https://pengyaochen.com/reader/ -> %{http_code} (200 is expected — see comment above)\n" \
  https://pengyaochen.com/reader/
