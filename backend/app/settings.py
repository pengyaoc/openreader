"""Paths and process-wide settings. Kept tiny and dependency-free."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("READER_CONFIG", REPO_ROOT / "config" / "feeds.yaml"))
DB_PATH = Path(os.environ.get("READER_DB", REPO_ROOT / "data" / "reader.db"))
MEDIA_DIR = Path(os.environ.get("READER_MEDIA", REPO_ROOT / "data" / "media"))

# type=imap sources. Deliberately env-only, never feeds.yaml — see
# app.config._check_no_credentials. All three must be set for IMAP refresh
# to run; a partial set is treated as "not configured" (see api/refresh_api).
IMAP_HOST = os.environ.get("READER_IMAP_HOST")
IMAP_USER = os.environ.get("READER_IMAP_USER")
IMAP_PASSWORD = os.environ.get("READER_IMAP_PASSWORD")

# Hard write-lock for every config-mutating endpoint (raw-YAML PUT
# /api/config and the structured source add/edit/delete endpoints) — set on
# the VM so the unauthenticated write path can't be used to rewrite
# feeds.yaml (or flip llm.enabled back on) over the internet. Config edits
# go through SSH instead.
READONLY_CONFIG = os.environ.get("READER_READONLY_CONFIG") == "1"
