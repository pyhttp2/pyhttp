"""Scheduled monitors: picked requests sent again on an interval, in the background of the
running app. Each check stores a compact result row (no run folders, no history entries, so
a monitor every minute does not flood the disk); failures can ping a webhook.

A check fails when any request errors, answers 4xx/5xx, or fails one of its tests —
the same rule the collection runner uses.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from . import USER_AGENT, db, runner
from .templating import build_context

TICK_SECONDS = 5.0  # how often the scheduler looks for due monitors


async def run_check(monitor: dict[str, Any]) -> dict[str, Any]:
    """Send the monitor's requests in order and store one check row. Returns the check."""
    started = time.time()
    results: list[dict[str, Any]] = []
    failed = 0
    prev: dict[str, Any] | None = None
    for rid in monitor.get("request_ids") or []:
        req = db.get_request(rid)
        if not req:
            results.append({"request_id": rid, "name": "(deleted request)", "ok": False,
                            "error": "the request no longer exists"})
            failed += 1
            continue
        for item in runner.expand(req):
            variables, _env = db.effective_variables(item.get("folder_id"))
            ctx = build_context(variables, prev)
            result = await runner.execute(item, ctx)
            prev = runner.prev_context(result)
            ok = result["status"] != "error" and (result.get("status_code") or 0) < 400 \
                and not result.get("tests_failed")
            if not ok:
                failed += 1
            results.append({
                "request_id": rid, "name": runner.display_name(item), "method": item.get("method", ""),
                "ok": ok, "status_code": result.get("status_code"),
                "duration_ms": result.get("duration_ms"), "error": result.get("error"),
                "tests_failed": result.get("tests_failed", 0),
            })
    check = {"ts": started, "ok": failed == 0, "failed": failed, "total": len(results),
             "duration_ms": round((time.time() - started) * 1000, 1), "results": results}
    stored = db.add_monitor_check(monitor["id"], check)
    db.update_monitor(monitor["id"], last_run_at=started, last_status="fail" if failed else "pass")
    if failed and (monitor.get("webhook_url") or "").strip():
        await _webhook(monitor, check)
    return stored


async def _webhook(monitor: dict[str, Any], check: dict[str, Any]) -> None:
    """POST the failure to the monitor's webhook. Errors are swallowed: an unreachable
    webhook must never break the schedule."""
    payload = {
        "monitor": monitor["name"], "monitor_id": monitor["id"], "status": "fail",
        "failed": check["failed"], "total": check["total"], "ts": check["ts"],
        "results": [r for r in check["results"] if not r["ok"]],
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
            await client.post(monitor["webhook_url"].strip(), json=payload)
    except Exception:  # noqa: BLE001
        pass


class MonitorScheduler:
    """Runs due monitors from the app's event loop. One check per monitor at a time."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running: set[str] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the schedule must survive anything
                pass
            await asyncio.sleep(TICK_SECONDS)

    def tick(self, now: float | None = None) -> list[asyncio.Task[Any]]:
        """Start a check task for every due monitor; called by the loop and by tests."""
        tasks = []
        for monitor in db.due_monitors(now if now is not None else time.time()):
            if monitor["id"] in self._running:
                continue
            self._running.add(monitor["id"])
            # claim at once, so the monitor is not due again while its check runs
            db.update_monitor(monitor["id"], last_run_at=time.time())
            tasks.append(asyncio.create_task(self._run(monitor)))
        return tasks

    async def _run(self, monitor: dict[str, Any]) -> None:
        try:
            await run_check(monitor)
        except Exception:  # noqa: BLE001
            db.update_monitor(monitor["id"], last_status="fail")
        finally:
            self._running.discard(monitor["id"])
