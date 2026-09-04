"""Per-request timing instrumentation.

Added 2026-09-03 to chase down an intermittent 2-3s gap between the frontend
shell painting and the article list rendering (see docs/WORKLOG.md). Nothing
on the deployment recorded request duration before this — Apache's
LogFormat had no %D, uvicorn's own access log has no timing — so a slow
open left no trace to diagnose after the fact.

Two things per API request:
  - A `Server-Timing` response header, so the browser's own Network panel /
    `PerformanceResourceTiming.serverTiming` can attribute time to the
    server without guessing at network vs. server latency.
  - A log line including the major-page-fault delta across the request
    (from /proc/self/stat field 12 — RSS pages faulted in from disk, not
    from the page cache). A live hypothesis is that the app's uvicorn
    worker gets swapped out during idle gaps on the box's tight ~964MB RAM,
    and a slow cold request has to fault its heap back in from a
    pd-standard disk. A large majflt delta on a slow request confirms that;
    zero delta rules it out. /proc/self/stat isn't available on macOS/BSD,
    so this degrades to duration-only there rather than failing.
"""
from __future__ import annotations

import time

from starlette.types import ASGIApp, Receive, Scope, Send


def _majflt() -> int | None:
    try:
        with open("/proc/self/stat") as f:
            # Field 12 (1-indexed) is majflt. The comm field (field 2) is
            # parenthesized and may itself contain spaces, so split from the
            # last ')' rather than by naive whitespace splitting.
            data = f.read()
            after_comm = data.rsplit(")", 1)[1]
            fields = after_comm.split()
            # fields[0] is field 3 (state); majflt is field 12, i.e. index 9.
            return int(fields[9])
    except (OSError, IndexError, ValueError):
        return None


class TimingMiddleware:
    """Pure ASGI middleware, same shape as AuthMiddleware (app/auth.py).
    Only instruments /api/* — static asset timing isn't the question here,
    and StaticFiles' own send() path doesn't need wrapping."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        faults_before = _majflt()
        status_holder: dict[str, int] = {}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - start) * 1000
                headers = message.setdefault("headers", [])
                headers.append(
                    (b"server-timing", f"app;dur={elapsed_ms:.1f}".encode())
                )
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            faults_after = _majflt()
            fault_delta = (
                faults_after - faults_before
                if faults_before is not None and faults_after is not None
                else None
            )
            print(
                f"TIMING {scope['method']} {scope['path']} "
                f"status={status_holder.get('status', '?')} "
                f"dur_ms={elapsed_ms:.1f} majflt_delta={fault_delta}",
                flush=True,
            )
