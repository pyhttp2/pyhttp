"""In-memory log of every request the backend sends (shown in the UI console).

Kept in a bounded ring buffer: it lives as long as the app is running and is
never written to disk.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

MAX_ENTRIES = 500
MAX_BODY_CHARS = 64_000

_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)
_lock = threading.Lock()
_seq = 0


def _clip(text: str | None) -> tuple[str, bool]:
    text = text or ""
    if len(text) > MAX_BODY_CHARS:
        return text[:MAX_BODY_CHARS], True
    return text, False


def record(result: dict[str, Any], *, source: str, request_name: str = "",
           body_name: str | None = None, environment: str | None = None) -> dict[str, Any]:
    """Store one executed request. `result` is what runner.execute() returned."""
    global _seq
    req = result.get("request") or {}
    resp = result.get("response")
    req_body, req_cut = _clip(req.get("body"))
    entry: dict[str, Any] = {
        "ts": time.time(),
        "source": source,
        "request_name": request_name,
        "body_name": body_name,
        "environment": environment,
        "method": req.get("method", ""),
        "url": req.get("url", ""),
        "status": result.get("status"),
        "status_code": result.get("status_code"),
        "reason": result.get("reason") or "",
        "duration_ms": round(result.get("duration_ms") or 0, 2),
        "error": result.get("error"),
        "request": {
            "headers": [list(h) for h in req.get("headers") or []],
            "body": req_body,
            "body_truncated": req_cut,
        },
        "response": None,
    }
    if resp:
        body, cut = _clip(resp.get("body_text"))
        entry["response"] = {
            "headers": [list(h) for h in resp.get("headers") or []],
            "body": body,
            "body_truncated": cut or bool(resp.get("truncated")),
            "size": resp.get("size"),
            "content_type": resp.get("content_type"),
            "is_json": resp.get("is_json"),
            "http_version": resp.get("http_version"),
            "redirects": resp.get("redirects") or [],
        }
    with _lock:
        _seq += 1
        entry["seq"] = _seq
        _entries.append(entry)
    return entry


def entries(after: int = 0) -> dict[str, Any]:
    with _lock:
        items = [e for e in _entries if e["seq"] > after]
        return {"entries": items, "last_seq": _seq, "count": len(_entries)}


def clear() -> None:
    with _lock:
        _entries.clear()
