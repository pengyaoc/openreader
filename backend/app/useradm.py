"""Account admin CLI: `uv run python -m app.useradm <command>`.

Deliberately lives in `backend/app/` rather than the repo-root `scripts/`,
which is where a CLI would otherwise belong: scripts/deploy.sh rsyncs only
backend/app, pyproject.toml, uv.lock and .python-version to the VM, so a
script under scripts/ would be unrunnable in production — which is exactly
where accounts get created and passwords get rotated.

There is no account UI and no HTTP password-change endpoint (see
app/auth.py); this file is the whole account-management story. Password
input never touches argv by default — getpass prompts twice — but --hash
accepts a pre-computed bcrypt hash for the case where you already have one
(`htpasswd -nbB`, or another account's, via copy-password).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from getpass import getpass

import bcrypt

from app import settings
from app.db import connect, init_schema


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _find(conn: sqlite3.Connection, username: str) -> sqlite3.Row | tuple | None:
    return conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()


def _require(conn: sqlite3.Connection, username: str):
    row = _find(conn, username)
    if row is None:
        sys.exit(f"no such user: {username!r} (run `useradm list`)")
    return row


def _resolve_hash(args) -> str:
    """--hash wins; otherwise prompt twice and bcrypt locally. Returned
    value is always a bcrypt hash string, never a plaintext password."""
    if args.hash:
        return args.hash
    first = getpass("password: ")
    if not first:
        sys.exit("empty password")
    if first != getpass("confirm: "):
        sys.exit("passwords do not match")
    return bcrypt.hashpw(first.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def cmd_list(conn: sqlite3.Connection, args) -> None:
    rows = conn.execute(
        """SELECT u.id, u.username, u.password_hash, u.created_at, u.password_changed_at,
                  (SELECT COUNT(*) FROM article_states s
                    WHERE s.user_id = u.id AND s.is_read = 1),
                  (SELECT COUNT(*) FROM article_states s
                    WHERE s.user_id = u.id AND s.is_starred = 1)
           FROM users u ORDER BY u.id"""
    ).fetchall()
    print(f"{'id':>3}  {'username':<20} {'pw':<4} {'read':>6} {'starred':>8}  created")
    for uid, name, pw_hash, created, changed, n_read, n_starred in rows:
        stamp = changed or created
        print(f"{uid:>3}  {name:<20} {'yes' if pw_hash else 'NO':<4} "
              f"{n_read:>6} {n_starred:>8}  {stamp}")


def cmd_add(conn: sqlite3.Connection, args) -> None:
    if _find(conn, args.username) is not None:
        sys.exit(f"user {args.username!r} already exists")
    password_hash = _resolve_hash(args)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (args.username, password_hash, _now()),
    )
    conn.commit()
    print(f"created user {args.username!r} as id {cur.lastrowid}")


def cmd_set_password(conn: sqlite3.Connection, args) -> None:
    row = _require(conn, args.username)
    conn.execute(
        "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
        (_resolve_hash(args), _now(), row[0]),
    )
    conn.commit()
    # Existing session cookies stay valid: they carry only a user id and an
    # expiry, with no password material. To invalidate every live session,
    # rotate READER_SESSION_SECRET instead (see app/auth.py).
    print(f"password updated for {row[1]!r}; existing sessions remain valid")


def cmd_copy_password(conn: sqlite3.Connection, args) -> None:
    src = _require(conn, args.source)
    dst = _require(conn, args.target)
    if not src[2]:
        sys.exit(f"{src[1]!r} has no password to copy")
    conn.execute(
        "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
        (src[2], _now(), dst[0]),
    )
    conn.commit()
    print(f"copied {src[1]!r}'s password hash to {dst[1]!r}")


def cmd_link(conn: sqlite3.Connection, args) -> None:
    """Attaches an email to an existing account, so the first
    trusted_header login with that email upserts onto this row instead
    of creating a new one — preserves article_states for accounts that
    predate this login system. See docs/WORKLOG.md, 2026-09-05."""
    row = _require(conn, args.username)
    conn.execute(
        "UPDATE users SET email = ? WHERE id = ?",
        (args.email.strip().lower(), row[0]),
    )
    conn.commit()
    print(f"linked {args.username!r} to {args.email!r}")


def cmd_rename(conn: sqlite3.Connection, args) -> None:
    row = _require(conn, args.old)
    if _find(conn, args.new) is not None:
        sys.exit(f"user {args.new!r} already exists")
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (args.new, row[0]))
    conn.commit()
    # Sessions survive a rename for the same reason they survive a password
    # change: the cookie identifies the account by id, not by name.
    print(f"renamed user {row[0]} from {row[1]!r} to {args.new!r}")


# No delete-user command on purpose: users.id cascades into article_states,
# so deleting an account silently discards its entire read/starred history.
# That is not a thing to put one flag away from a typo.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.useradm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show accounts and their state counts").set_defaults(fn=cmd_list)

    add = sub.add_parser("add", help="create an account")
    add.add_argument("username")
    add.add_argument("--hash", help="pre-computed bcrypt hash; prompts if omitted")
    add.set_defaults(fn=cmd_add)

    setpw = sub.add_parser("set-password", help="change an account's password")
    setpw.add_argument("username")
    setpw.add_argument("--hash", help="pre-computed bcrypt hash; prompts if omitted")
    setpw.set_defaults(fn=cmd_set_password)

    copy = sub.add_parser("copy-password", help="reuse one account's hash for another")
    copy.add_argument("source")
    copy.add_argument("target")
    copy.set_defaults(fn=cmd_copy_password)

    link = sub.add_parser("link", help="attach an email to an existing account")
    link.add_argument("username")
    link.add_argument("email")
    link.set_defaults(fn=cmd_link)

    rename = sub.add_parser("rename", help="change an account's username")
    rename.add_argument("old")
    rename.add_argument("new")
    rename.set_defaults(fn=cmd_rename)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    conn = connect(settings.DB_PATH)
    # init_schema so this is usable against a DB that doesn't exist yet —
    # the same call the app makes on startup, and idempotent.
    init_schema(conn)
    try:
        args.fn(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
