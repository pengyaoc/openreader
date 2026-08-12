"""Paths and process-wide settings. Kept tiny and dependency-free."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("READER_CONFIG", REPO_ROOT / "config" / "feeds.yaml"))
DB_PATH = Path(os.environ.get("READER_DB", REPO_ROOT / "data" / "reader.db"))
MEDIA_DIR = Path(os.environ.get("READER_MEDIA", REPO_ROOT / "data" / "media"))
TOKEN_PATH = Path(os.environ.get("READER_GMAIL_TOKEN", REPO_ROOT / "data" / "token.json"))
