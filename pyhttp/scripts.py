"""Optional Python pre-request and test scripts (the Python answer to Postman's JS scripts).

Scripts run in-process with a small `py` object (`pm` is an alias for people
coming from Postman). They are the user's own code on their own machine, so
there is no sandbox; a script that hangs is abandoned after SCRIPT_TIMEOUT
seconds and reported as failed.

Pre-request script:
    py.variables.set("ts", "123")          # this request only
    py.environment.set("token", "abc")     # saved in the active environment
    py.globals.set("base", "https://x")    # saved globally
    py.variables.get("name")               # effective value (globals < folder < env < local)
    py.request.headers["X-Trace"] = py.variables.get("trace")   # add / change a header
    py.request.url, py.request.method, py.request.body            # editable
    py.request.params["page"] = "2"

Test script:
    py.response.status_code, py.response.text, py.response.json(), py.response.headers["content-type"]
    py.response.time_ms, py.response.ok
    token = py.response.json().data.token        # dotted access like JavaScript; missing -> None
    py.test("status is 200", py.response.status_code == 200)
    py.test("has token", lambda: py.expect(token).to_be_a(str))
    py.expect(x).to_equal(y) / .to_not_equal(y) / .to_contain(y) / .to_be_true() / .to_be_above(n) / .to_match(regex)
    py.environment.set("token", token)

JavaScript habits that are accepted as-is: `const`/`let`/`var`, a trailing `;`,
`true`/`false`/`null`, and `console.log(...)`. Everything else is Python
(`and`/`or`/`not`, `==`, `lambda:` instead of `function () {}`).
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from typing import Any, Callable

SCRIPT_TIMEOUT = 15.0

# Lines that look like JavaScript (Postman habit). Used only to make the error message helpful.
_JS_HINTS = re.compile(r"=>|\bfunction\s*\(|!==|===|\|\||&&|^\s*\}|\{\s*$")


class _Expect:
    def __init__(self, value: Any) -> None:
        self.v = value

    def _fail(self, msg: str) -> None:
        raise AssertionError(msg)

    def to_equal(self, other: Any) -> "_Expect":
        if self.v != other:
            self._fail(f"expected {self.v!r} to equal {other!r}")
        return self

    def to_not_equal(self, other: Any) -> "_Expect":
        if self.v == other:
            self._fail(f"expected {self.v!r} to differ from {other!r}")
        return self

    def to_contain(self, item: Any) -> "_Expect":
        try:
            ok = item in self.v
        except TypeError:
            ok = False
        if not ok:
            self._fail(f"expected {self.v!r} to contain {item!r}")
        return self

    def to_be_true(self) -> "_Expect":
        if not self.v:
            self._fail(f"expected {self.v!r} to be truthy")
        return self

    def to_be_false(self) -> "_Expect":
        if self.v:
            self._fail(f"expected {self.v!r} to be falsy")
        return self

    def to_be_none(self) -> "_Expect":
        if self.v is not None:
            self._fail(f"expected {self.v!r} to be None")
        return self

    def to_be_above(self, n: float) -> "_Expect":
        if not self.v > n:
            self._fail(f"expected {self.v!r} to be above {n!r}")
        return self

    def to_be_below(self, n: float) -> "_Expect":
        if not self.v < n:
            self._fail(f"expected {self.v!r} to be below {n!r}")
        return self

    def to_match(self, pattern: str) -> "_Expect":
        if re.search(pattern, str(self.v)) is None:
            self._fail(f"expected {self.v!r} to match /{pattern}/")
        return self

    def to_be_a(self, kind: type | tuple[type, ...]) -> "_Expect":
        if not isinstance(self.v, kind):
            self._fail(f"expected {self.v!r} to be a {getattr(kind, '__name__', kind)}")
        return self

    def to_have_length(self, n: int) -> "_Expect":
        if len(self.v) != n:
            self._fail(f"expected length {n}, got {len(self.v)}")
        return self

    def to_have_key(self, key: str) -> "_Expect":
        if not isinstance(self.v, dict) or key not in self.v:
            self._fail(f"expected key {key!r} in {self.v!r}")
        return self


class _Scope:
    """py.variables / py.environment / py.globals."""

    def __init__(self, store: dict[str, str], on_set: Callable[[str, str], None] | None = None) -> None:
        self._store = store
        self._on_set = on_set
        self.changes: list[tuple[str, str]] = []

    def get(self, name: str, default: Any = None) -> Any:
        return self._store.get(name, default)

    def set(self, name: str, value: Any) -> None:
        text = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        self._store[name] = text
        self.changes.append((name, text))
        if self._on_set:
            self._on_set(name, text)

    def has(self, name: str) -> bool:
        return name in self._store

    def unset(self, name: str) -> None:
        self._store.pop(name, None)

    def to_dict(self) -> dict[str, str]:
        return dict(self._store)


class JsonObject(dict):
    """A dict whose keys are also attributes: r.json().data.token, like JavaScript.
    A missing attribute is None (JavaScript's undefined); r.json()["key"] stays strict."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        self.pop(name, None)

    @property
    def length(self) -> int:  # noqa: D401 - JavaScript habit
        return len(self)


class JsonArray(list):
    @property
    def length(self) -> int:
        return len(self)


def wrap_json(value: Any) -> Any:
    """Recursively wrap parsed JSON so dotted access works at every level."""
    if isinstance(value, dict):
        return JsonObject({k: wrap_json(v) for k, v in value.items()})
    if isinstance(value, list):
        return JsonArray(wrap_json(v) for v in value)
    return value


class _Console:
    def __init__(self, log: Callable[..., None]) -> None:
        self.log = log
        self.info = log
        self.warn = log
        self.error = log


class _Request:
    def __init__(self, req: dict[str, Any]) -> None:
        self.method: str = req.get("method", "GET")
        self.url: str = req.get("url", "")
        self.body: str = req.get("body", "") or ""
        self.body_type: str = req.get("body_type", "none")
        self.headers: dict[str, str] = {r["key"]: r.get("value", "") for r in req.get("headers") or [] if r.get("enabled", True) and r.get("key")}
        self.params: dict[str, str] = {r["key"]: r.get("value", "") for r in req.get("params") or [] if r.get("enabled", True) and r.get("key")}
        self.name: str = req.get("name", "")

    def apply(self, req: dict[str, Any]) -> dict[str, Any]:
        out = dict(req)
        out["method"] = self.method
        out["url"] = self.url
        out["body"] = self.body
        out["body_type"] = self.body_type
        out["headers"] = [{"key": k, "value": v, "enabled": True} for k, v in self.headers.items()]
        out["params"] = [{"key": k, "value": v, "enabled": True} for k, v in self.params.items()]
        return out


class _Response:
    def __init__(self, result: dict[str, Any]) -> None:
        resp = result.get("response") or {}
        self.status_code: int | None = result.get("status_code")
        self.code = self.status_code
        self.reason: str = result.get("reason") or ""
        self.text: str = resp.get("body_text") or ""
        self.headers: dict[str, str] = {k.lower(): v for k, v in resp.get("headers") or []}
        self.time_ms: float = result.get("duration_ms") or 0.0
        self.size: int = resp.get("size") or 0
        self.error: str | None = result.get("error")
        self.ok: bool = result.get("status") == "ok" and (self.status_code or 0) < 400
        self._json = result.get("_json")

    def json(self) -> Any:
        if self._json is None and self.text:
            self._json = json.loads(self.text)
        if not isinstance(self._json, (JsonObject, JsonArray)):
            self._json = wrap_json(self._json)
        return self._json

    def header(self, name: str, default: Any = None) -> Any:
        return self.headers.get(name.lower(), default)


class PY:
    def __init__(self, req: dict[str, Any], variables: dict[str, str], result: dict[str, Any] | None,
                 set_var: Callable[[str, str, str], str] | None) -> None:
        self.request = _Request(req)
        self.response = _Response(result) if result is not None else None
        self.variables = _Scope(dict(variables))
        self.environment = _Scope({}, (lambda n, v: set_var(n, v, "env")) if set_var else None)
        self.globals = _Scope({}, (lambda n, v: set_var(n, v, "global")) if set_var else None)
        self.tests: list[dict[str, Any]] = []
        self.logs: list[str] = []
        self.info = {"request_name": req.get("name", ""), "iteration": req.get("iteration", 1)}
        self.data = wrap_json(dict(req.get("data_row") or {}))

    def test(self, name: str, condition: Any = True) -> bool:
        try:
            ok = condition() if callable(condition) else condition
            ok = True if ok is None else bool(ok)
            note = "" if ok else "condition was false"
        except AssertionError as exc:
            ok, note = False, str(exc) or "assertion failed"
        except Exception as exc:  # noqa: BLE001
            ok, note = False, f"{type(exc).__name__}: {exc}"
        self.tests.append({"name": str(name), "passed": ok, "note": note})
        return ok

    def expect(self, value: Any) -> _Expect:
        return _Expect(value)

    def log(self, *args: Any) -> None:
        self.logs.append(" ".join(str(a) for a in args))

    # variable lookups honour every scope that was set during the script
    def get(self, name: str, default: Any = None) -> Any:
        for scope in (self.variables, self.environment, self.globals):
            if scope.has(name):
                return scope.get(name)
        return default


PM = PY  # Postman-style alias


def _syntax_message(exc: SyntaxError, code: str, label: str) -> str:
    line_no = exc.lineno or 1
    lines = code.splitlines()
    src = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
    msg = f"Python syntax error in the {label} script, line {line_no}: {exc.msg}"
    if src:
        msg += f"  →  {src[:120]}"
    if _JS_HINTS.search(src) or _JS_HINTS.search(code):
        msg += ("  (scripts are Python: no function () {...} or =>, use `and`/`or`/`not` and `==`, "
                "and py.test(\"name\", condition) or py.test(\"name\", lambda: ...) instead of a callback)")
    return msg


_DECL = re.compile(r"^(\s*)(?:const|let|var)\s+(?=[A-Za-z_])", re.MULTILINE)
_SEMI = re.compile(r";\s*$", re.MULTILINE)


def pythonize(code: str) -> str:
    """Accept the JavaScript habits that have an obvious Python meaning:
    `const x = ...;` becomes `x = ...`, and a trailing `;` is dropped. Line count is unchanged."""
    code = code.replace("\r\n", "\n")
    code = _DECL.sub(r"\1", code)
    return _SEMI.sub("", code)


def _run(code: str, py: PY, label: str) -> dict[str, Any]:
    """Execute `code` with `py` (and `pm`) available. Never raises."""
    out: dict[str, Any] = {"ok": True, "error": None, "logs": py.logs, "timed_out": False}
    if not (code or "").strip():
        return out
    code = pythonize(code)
    try:
        compiled = compile(code, f"<{label} script>", "exec")
    except SyntaxError as exc:
        out["ok"] = False
        out["error"] = _syntax_message(exc, code, label)
        return out
    env = {"py": py, "pm": py, "json": json, "re": re, "print": py.log, "__name__": f"pyhttp_{label}",
           "true": True, "false": False, "null": None, "console": _Console(py.log)}

    def target() -> None:
        try:
            exec(compiled, env)  # noqa: S102 - user's own script
        except Exception as exc:  # noqa: BLE001
            tb = traceback.extract_tb(exc.__traceback__)
            loc = [f for f in tb if f.filename == f"<{label} script>"]
            where = f" (line {loc[-1].lineno})" if loc else ""
            out["ok"] = False
            out["error"] = f"{type(exc).__name__}: {exc}{where}"

    t = threading.Thread(target=target, daemon=True, name=f"pyhttp-{label}-script")
    t.start()
    t.join(SCRIPT_TIMEOUT)
    if t.is_alive():
        out.update({"ok": False, "timed_out": True, "error": f"script still running after {SCRIPT_TIMEOUT:.0f}s; abandoned"})
    return out


def run_pre_request(code: str, req: dict[str, Any], variables: dict[str, str],
                    set_var: Callable[[str, str, str], str] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Returns (possibly modified request, script report, local variable changes)."""
    py = PY(req, variables, None, set_var)
    report = _run(code, py, "pre-request")
    report["variables"] = dict(py.variables.changes)
    if not report["ok"]:
        return req, report, {}
    return py.request.apply(req), report, dict(py.variables.changes)


def run_tests(code: str, req: dict[str, Any], variables: dict[str, str], result: dict[str, Any],
              set_var: Callable[[str, str, str], str] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (tests from the script, script report)."""
    py = PY(req, variables, result, set_var)
    report = _run(code, py, "test")
    tests = list(py.tests)
    if not report["ok"]:
        tests.append({"name": "Test script", "passed": False, "note": report["error"] or "script error"})
    return tests, report
