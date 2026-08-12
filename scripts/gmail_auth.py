"""One-time Gmail OAuth consent. Run this once, locally, in a real browser
session — it is the ONLY place in this codebase that imports
google-auth-oauthlib. The server never runs this; it just reads the
refresh token this script writes.

Prerequisites (you do this once, in Google Cloud Console, not here):
  1. Create a Google Cloud project (or reuse one).
  2. Enable the "Gmail API" for it.
  3. Create OAuth 2.0 credentials of type "Desktop app".
  4. Download the client secret JSON and save it as
     config/gmail_client_secret.json (gitignored — see config/.gitignore).

What this script does:
  - Opens your browser to Google's consent screen, read-only Gmail scope
    only (https://www.googleapis.com/auth/gmail.readonly).
  - On approval, exchanges the auth code for a refresh token.
  - Writes { client_id, client_secret, refresh_token } to data/token.json.
    The server reads this file at refresh time to mint short-lived access
    tokens — it never sees your Google password and never gets a broader
    scope than gmail.readonly.

Usage:
    uv run --extra gmail-auth python ../scripts/gmail_auth.py
(from the backend/ directory, or adjust the path accordingly)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SECRET_PATH = REPO_ROOT / "config" / "gmail_client_secret.json"
TOKEN_PATH = REPO_ROOT / "data" / "token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "google-auth-oauthlib isn't installed. Run this with:\n"
            "  uv run --extra gmail-auth python scripts/gmail_auth.py",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not CLIENT_SECRET_PATH.exists():
        print(
            f"Missing {CLIENT_SECRET_PATH}.\n\n"
            "Create a Desktop-app OAuth client in Google Cloud Console "
            "(APIs & Services > Credentials), enable the Gmail API, "
            "download the client secret JSON, and save it at that path.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(
        json.dumps(
            {
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "refresh_token": creds.refresh_token,
            },
            indent=2,
        )
    )
    print(f"Wrote {TOKEN_PATH}. Gmail sources will now refresh alongside RSS ones.")


if __name__ == "__main__":
    main()
