"""Auto-refresh: a full refresh every `auto_refresh_hours` (default 12),
counted from the last full refresh, manual or automatic. It shares one
lock with the Refresh button and skips its turn while one is running."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import autorefresh
from app.api import refresh_api
from app.autorefresh import MIN_DELAY_S, auto_refresh_loop, latest_fetch, next_delay
from app.config import Config, parse_config
from app.db import connect, init_schema

T0 = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def test_config_default_is_twelve_hours_and_zero_parses():
    assert Config().auto_refresh_hours == 12
    assert parse_config("auto_refresh_hours: 0\n").auto_refresh_hours == 0


def test_next_delay_never_refreshed_runs_soon():
    assert next_delay(None, T0, 12) == MIN_DELAY_S


def test_next_delay_counts_from_last_full_refresh():
    assert next_delay(T0 - timedelta(hours=2), T0, 12) == pytest.approx(10 * 3600)


def test_next_delay_overdue_waits_the_minimum():
    # Just after a deploy or restart, give the process a minute first.
    assert next_delay(T0 - timedelta(days=3), T0, 12) == MIN_DELAY_S


def test_latest_fetch_reads_newest_source_fetch(tmp_path):
    conn = connect(tmp_path / "r.db")
    init_schema(conn)
    assert latest_fetch(conn) is None
    for key, at in (("a", T0 - timedelta(hours=5)), ("b", T0 - timedelta(hours=1))):
        conn.execute(
            "INSERT INTO sources (key, title, folder, type, last_fetched_at) VALUES (?, ?, '', 'rss', ?)",
            (key, key, at.isoformat()),
        )
    conn.commit()
    assert latest_fetch(conn) == T0 - timedelta(hours=1)


def _app(tmp_path, hours=12):
    conn = connect(tmp_path / "r.db")
    init_schema(conn)
    conn.close()
    return SimpleNamespace(
        state=SimpleNamespace(
            config=Config(auto_refresh_hours=hours),
            db_path=tmp_path / "r.db",
            refresh_lock=asyncio.Lock(),
            last_full_refresh=None,
        )
    )


@pytest.fixture
def fake_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh_api, "refresh_all", lambda conn, sources, **kw: calls.append(kw) or {"sources": []})
    monkeypatch.setattr(refresh_api, "hydrate_in_background", lambda db, scope: None)
    return calls


def test_full_refresh_records_time_single_source_does_not(tmp_path, fake_refresh):
    app = _app(tmp_path)
    asyncio.run(refresh_api.run_refresh(app, only_key="x"))
    assert app.state.last_full_refresh is None
    asyncio.run(refresh_api.run_refresh(app))
    assert app.state.last_full_refresh is not None
    assert len(fake_refresh) == 2


def test_run_refresh_without_wait_skips_while_busy(tmp_path, fake_refresh):
    app = _app(tmp_path)

    async def scenario():
        async with app.state.refresh_lock:
            return await refresh_api.run_refresh(app, wait=False)

    assert asyncio.run(scenario()) is None
    assert fake_refresh == []


class _Stop(Exception):
    pass


def _drive(app, runs_before_stop, clock, run):
    """Runs the loop with a fake clock: each sleep advances it, and the
    loop is stopped once `run` has been called `runs_before_stop` times or
    after 20 sleeps."""
    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        clock[0] += timedelta(seconds=s)
        if len(sleeps) > 20 or run.calls >= runs_before_stop:
            raise _Stop

    with pytest.raises(_Stop):
        asyncio.run(auto_refresh_loop(app, now=lambda: clock[0], sleep=sleep, run=run))
    return sleeps


class _Run:
    def __init__(self, app, clock, result=None, error=None):
        self.calls = 0
        self.app, self.clock, self.result, self.error = app, clock, result, error

    async def __call__(self, app, only_key=None, *, wait=True):
        assert wait is False and only_key is None
        self.calls += 1
        if self.error:
            raise self.error
        if self.result is not None:
            app.state.last_full_refresh = self.clock[0]
        return self.result


def test_loop_refreshes_then_waits_the_interval(tmp_path):
    app = _app(tmp_path)
    clock = [T0]
    run = _Run(app, clock, result={"sources": []})
    sleeps = _drive(app, 2, clock, run)
    assert run.calls == 2
    assert sleeps[0] == MIN_DELAY_S
    assert sleeps[1] == pytest.approx(12 * 3600)


def test_loop_off_when_zero_hours(tmp_path):
    app = _app(tmp_path, hours=0)
    clock = [T0]
    run = _Run(app, clock, result={"sources": []})
    _drive(app, 1, clock, run)
    assert run.calls == 0


def test_loop_waits_again_if_manual_refresh_happened_during_sleep(tmp_path):
    app = _app(tmp_path)
    app.state.last_full_refresh = T0 - timedelta(hours=11)
    clock = [T0]
    run = _Run(app, clock, result={"sources": []})

    sleeps = []

    async def sleep(s):
        sleeps.append(s)
        clock[0] += timedelta(seconds=s)
        if len(sleeps) == 1:
            # Someone pressed Refresh while the loop slept.
            app.state.last_full_refresh = clock[0] - timedelta(minutes=1)
        if len(sleeps) >= 2:
            raise _Stop

    with pytest.raises(_Stop):
        asyncio.run(auto_refresh_loop(app, now=lambda: clock[0], sleep=sleep, run=run))
    assert run.calls == 0
    assert sleeps[1] == pytest.approx(12 * 3600 - 60)


def test_loop_failure_does_not_spin(tmp_path):
    app = _app(tmp_path)
    clock = [T0]
    run = _Run(app, clock, error=RuntimeError("network down"))
    sleeps = _drive(app, 2, clock, run)
    assert run.calls == 2
    # After the failed run it waits the full interval, not the minimum.
    assert sleeps[1] == pytest.approx(12 * 3600)


def test_loop_busy_retries_after_the_minimum(tmp_path):
    app = _app(tmp_path)
    clock = [T0]
    run = _Run(app, clock, result=None)  # None = a manual refresh held the lock
    sleeps = _drive(app, 2, clock, run)
    assert sleeps[1] == MIN_DELAY_S


def test_lifespan_seeds_from_db_and_cancels(tmp_path, monkeypatch):
    from app.main import create_app

    started = []

    async def fake_loop(app, **kw):
        started.append(app.state.last_full_refresh)
        await asyncio.Event().wait()

    monkeypatch.setattr(autorefresh, "auto_refresh_loop", fake_loop)
    db = tmp_path / "r.db"
    conn = connect(db)
    init_schema(conn)
    conn.execute(
        "INSERT INTO sources (key, title, folder, type, last_fetched_at) VALUES ('a', 'a', '', 'rss', ?)",
        ((T0).isoformat(),),
    )
    conn.commit()
    conn.close()
    from starlette.testclient import TestClient

    app = create_app(db_path=db, config=Config(), require_auth=False, auto_refresh=True)
    with TestClient(app):
        pass
    assert started == [T0]


def test_create_app_without_auto_refresh_starts_nothing(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(autorefresh, "auto_refresh_loop", lambda *a, **k: pytest.fail("started"))
    with TestClient(create_app(db_path=tmp_path / "r.db", config=Config(), require_auth=False)):
        pass
