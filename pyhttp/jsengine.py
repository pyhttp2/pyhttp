"""JavaScript scripts (Postman-compatible `pm` API) in an embedded engine.

Engines, first one found wins:
  - quickjs  (python-quickjs, ~1 MB; used in the packaged builds)
  - mini_racer (V8, ~15 MB; wheels for every Python version, handy for development)

The engine gets a JSON state (request, response, variables), the `pm` runtime
from pm.js, then the user's script. Results come back as JSON: tests, logs,
variable changes, the edited request (pre-request scripts) and any error.
No host callbacks are needed, so both engines work the same way.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SCRIPT_TIMEOUT = 15.0
MEMORY_LIMIT = 256 * 1024 * 1024
_PRELUDE_PATH = Path(__file__).with_name("pm.js")
_prelude_cache: str | None = None
_USER_LINE_RE = re.compile(r"user\.js:(\d+)")
_ANY_LINE_RE = re.compile(r"(?:<input>|<anonymous>|<eval>|user\.js):(\d+)")


class EngineMissing(RuntimeError):
    pass


def _prelude() -> str:
    global _prelude_cache
    if _prelude_cache is None:
        _prelude_cache = _PRELUDE_PATH.read_text(encoding="utf-8")
    return _prelude_cache


def _js_json(value: Any) -> str:
    # JSON is valid JS except for the two line separators
    return json.dumps(value).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


# ------------------------------------------------------------------ engines


class _QuickJS:
    name = "quickjs"

    def __init__(self, timeout: float) -> None:
        import quickjs  # type: ignore

        self._mod = quickjs
        self.ctx = quickjs.Context()
        self.ctx.set_time_limit(timeout)
        self.ctx.set_memory_limit(MEMORY_LIMIT)
        self.ctx.set_max_stack_size(4 * 1024 * 1024)

    def eval(self, code: str) -> Any:
        try:
            return self.ctx.eval(code)
        except self._mod.JSException as exc:  # pragma: no cover - engine specific
            raise JSError(str(exc)) from exc

    def run_jobs(self) -> None:
        for _ in range(1000):
            try:
                if not self.ctx.execute_pending_job():
                    break
            except Exception as exc:  # noqa: BLE001
                raise JSError(str(exc)) from exc

    def is_timeout(self, exc: Exception) -> bool:
        return "interrupted" in str(exc).lower() or "time limit" in str(exc).lower()


class _MiniRacer:
    name = "v8"

    def __init__(self, timeout: float) -> None:
        import py_mini_racer  # type: ignore

        self._mod = py_mini_racer
        self.ctx = py_mini_racer.MiniRacer()
        self.timeout = timeout
        try:
            self.ctx.set_hard_memory_limit(MEMORY_LIMIT)
        except Exception:  # noqa: BLE001 - older versions
            pass

    def eval(self, code: str) -> Any:
        try:
            return self.ctx.eval(code, timeout_sec=self.timeout)
        except self._mod.JSTimeoutException as exc:
            raise JSTimeout(str(exc)) from exc
        except self._mod.JSEvalException as exc:
            raise JSError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - JSParseException etc.
            raise JSError(str(exc)) from exc

    def run_jobs(self) -> None:  # V8 runs microtasks at the end of each eval
        return None

    def is_timeout(self, exc: Exception) -> bool:
        return isinstance(exc, JSTimeout)


class JSError(Exception):
    pass


class JSTimeout(JSError):
    pass


def _engine(timeout: float) -> _QuickJS | _MiniRacer:
    errors = []
    for cls in (_QuickJS, _MiniRacer):
        try:
            return cls(timeout)
        except ImportError as exc:
            errors.append(str(exc))
    raise EngineMissing("No JavaScript engine installed. Run: pip install quickjs  (or: pip install mini-racer). "
                        "Python scripts work without one.")


def engine_name() -> str | None:
    try:
        return _engine(1.0).name
    except EngineMissing:
        return None


# ------------------------------------------------------------------ run


def _clean_error(text: str, offset: int) -> tuple[str, int | None]:
    """Strip engine noise from an error string and translate the line number back to the script."""
    text = text.strip()
    line = None
    # V8 names the user's eval "user.js" (sourceURL). Otherwise the outermost frame
    # (last in the stack) is the user's script: frames above it belong to pm.js.
    found = _USER_LINE_RE.findall(text) or _ANY_LINE_RE.findall(text)
    if found:
        n = int(found[0] if _USER_LINE_RE.search(text) else found[-1]) - offset
        if n >= 1:
            line = n
    # first line of the stack is the message; keep "at ..." frames out of the note
    first = text.splitlines()[0] if text else "error"
    first = re.sub(r"^<anonymous>:\d+:\s*", "", first)
    first = re.sub(r"^\s*at\s.*$", "", first).strip() or "error"
    if line:
        first += f" (line {line})"
    return first, line


def run(code: str, state: dict[str, Any], *, event: str, timeout: float = SCRIPT_TIMEOUT) -> dict[str, Any]:
    """Run a user script. Never raises for script problems; returns
    {ok, error, line, logs, tests, vars, unset, request, next_request, timed_out, engine}.

    The engine runs in a worker thread: the caller is usually inside the asyncio loop,
    where V8's synchronous timeout is refused, and a long script must not block the server.
    """
    if not (code or "").strip():
        return _empty()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pyhttp-js") as pool:
        return pool.submit(_run_inner, code, state, event, timeout).result()


def _empty() -> dict[str, Any]:
    return {"ok": True, "error": None, "line": None, "logs": [], "tests": [], "vars": {},
            "unset": {}, "request": None, "next_request": None, "timed_out": False, "engine": None}


def _run_inner(code: str, state: dict[str, Any], event: str, timeout: float) -> dict[str, Any]:
    out = _empty()
    try:
        eng = _engine(timeout)
    except EngineMissing as exc:
        out.update({"ok": False, "error": str(exc)})
        return out
    out["engine"] = eng.name
    state = dict(state)
    state["eventName"] = event
    started = time.perf_counter()
    try:
        eng.eval(f"var __STATE = {_js_json(state)};\n" + _prelude())
    except JSError as exc:
        out.update({"ok": False, "error": f"pm runtime failed to load: {exc}"})
        return out
    user = code.replace("\r\n", "\n")
    wrapped = "try {\n" + user + "\n} catch (__e) { __R.error = __formatError(__e); }\n//# sourceURL=user.js"
    try:
        eng.eval(wrapped)
        eng.run_jobs()
        eng.eval("__settle();")
        eng.run_jobs()
        raw = eng.eval("__result();")
    except JSError as exc:
        if eng.is_timeout(exc):
            out.update({"ok": False, "timed_out": True,
                        "error": f"script still running after {timeout:g}s; stopped"})
            return out
        msg, line = _clean_error(str(exc), 1)
        out.update({"ok": False, "error": msg, "line": line})
        return out
    try:
        res = json.loads(raw if isinstance(raw, str) else str(raw))
    except (TypeError, ValueError):
        out.update({"ok": False, "error": "script produced an unreadable result"})
        return out
    out["logs"] = [str(x) for x in res.get("logs") or []]
    out["tests"] = [{"name": str(t.get("name", "")), "passed": bool(t.get("passed")), "note": str(t.get("note") or "")}
                    for t in res.get("tests") or []]
    out["vars"] = res.get("vars") or {}
    out["unset"] = res.get("unset") or {}
    out["request"] = res.get("request")
    out["next_request"] = res.get("nextRequest")
    out["duration_ms"] = (time.perf_counter() - started) * 1000
    if res.get("error"):
        msg, line = _clean_error(str(res["error"]), 1)
        out.update({"ok": False, "error": msg, "line": line})
    return out
