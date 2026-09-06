"""Tests for app.useradm's link subcommand (consolidated login,
2026-09-05). Other useradm commands (add/set-password/copy-password) are
untested here — they're deleted once trusted_header login is verified
live; see docs/WORKLOG.md.
"""
from app.db import connect, init_schema
from app.useradm import build_parser


def test_link_attaches_email_to_existing_account(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)  # creates the default 'reader' account via ensure_default_user

    args = build_parser().parse_args(["link", "reader", "me@example.com"])
    args.fn(conn, args)

    row = conn.execute("SELECT email FROM users WHERE username = 'reader'").fetchone()
    assert row[0] == "me@example.com"


def test_link_lowercases_and_strips_the_email(tmp_path):
    conn = connect(tmp_path / "reader.db")
    init_schema(conn)  # creates the default 'reader' account via ensure_default_user

    args = build_parser().parse_args(["link", "reader", "  Me@Example.com  "])
    args.fn(conn, args)

    row = conn.execute("SELECT email FROM users WHERE username = 'reader'").fetchone()
    assert row[0] == "me@example.com"


def test_link_exits_for_unknown_username(tmp_path):
    import pytest

    conn = connect(tmp_path / "reader.db")
    init_schema(conn)

    args = build_parser().parse_args(["link", "nosuchuser", "me@example.com"])
    with pytest.raises(SystemExit):
        args.fn(conn, args)
