"""Automatic refresh: a full refresh of every source every
`auto_refresh_hours` (feeds.yaml, default 12, 0 turns it off).

The clock counts from the last full refresh, manual or automatic
(app.state.last_full_refresh, set by refresh_api.run_refresh). After a
restart it starts from the newest source fetch in the DB, so a deploy
doesn't trigger an extra refresh. A one-source refresh doesn't reset it.

It shares app.state.refresh_lock with the Refresh button. If a refresh is
already running when the timer fires, the automatic one skips its turn
and checks again a minute later.

Runs as one asyncio task in the app's lifespan, in production only
(create_app(auto_refresh=True)). The refresh itself runs off the event
loop, the same way the button's does.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

# Shortest wait before a refresh: just after startup, and between retries
# while another refresh holds the lock.
MIN_DELAY_S = 60.0
# How often to look again while auto-refresh is turned off (0 hours), so
# turning it back on in feeds.yaml takes effect without a restart.
OFF_RECHECK_S = 600.0


def latest_fetch(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT MAX(last_fetched_at) FROM sources").fetchone()
    if not row or not row[0]:
        return None
    at = datetime.fromisoformat(row[0])
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def next_delay(last_full: datetime | None, now: datetime, hours: float) -> float:
    if last_full is None:
        return MIN_DELAY_S
    due = last_full + timedelta(hours=hours)
    return max(MIN_DELAY_S, (due - now).total_seconds())


def _log(msg: str) -> None:
    print(f"auto-refresh: {msg}", file=sys.stderr, flush=True)


async def auto_refresh_loop(
    app,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    run=None,
) -> None:
    if run is None:
        from app.api.refresh_api import run_refresh as run
    state = app.state
    while True:
        hours = state.config.auto_refresh_hours
        if hours <= 0:
            await sleep(OFF_RECHECK_S)
            continue
        await sleep(next_delay(state.last_full_refresh, now(), hours))

        # The settings may have changed, or someone pressed Refresh, while
        # this slept.
        hours = state.config.auto_refresh_hours
        if hours <= 0 or next_delay(state.last_full_refresh, now(), hours) > MIN_DELAY_S:
            continue
        started = now()
        try:
            report = await run(app, wait=False)
        except Exception as exc:  # noqa: BLE001 — a failed run must not end the loop
            _log(f"failed: {exc!r}")
            # Wait a full interval before trying again, rather than
            # retrying every minute against a feed host that is down.
            state.last_full_refresh = started
            continue
        if report is None:
            _log("another refresh is running; trying again shortly")
            continue
        errors = sum(1 for s in report.get("sources", []) if s.get("status") == "error")
        _log(f"done: {len(report.get('sources', []))} sources, {errors} errors")
