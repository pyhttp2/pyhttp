"""Code-free tests (assertions) and value extraction, evaluated after a response.

A request can carry a list of tests such as "status equals 200" or
"json data.token exists", and a list of extractions such as "set env variable
`token` from json `data.token`". Both are plain dicts so they survive
import/export, and neither needs a script engine.

Test shape:
    {"type": "status"|"time"|"header"|"body"|"json",
     "target": "<header name or json path>", "op": "<operator>",
     "value": "<expected>", "enabled": true}

Extraction shape:
    {"name": "token", "source": "json"|"header"|"status"|"body"|"regex",
     "path": "data.token" | "Set-Cookie" | "<regex with one group>",
     "scope": "env"|"global", "enabled": true}
"""
from __future__ import annotations

import json
import re
from typing import Any

from .templating import _Missing, _walk, to_text

OPS: dict[str, list[str]] = {
    "status": ["equals", "not_equals", "less_than", "greater_than", "is_2xx", "is_3xx", "is_4xx", "is_5xx", "in"],
    "time": ["less_than", "greater_than"],
    "header": ["exists", "not_exists", "equals", "not_equals", "contains", "not_contains", "matches"],
    "body": ["contains", "not_contains", "equals", "not_equals", "matches", "is_json", "is_empty", "not_empty"],
    "json": ["exists", "not_exists", "equals", "not_equals", "contains", "not_contains", "matches",
             "type_is", "length_equals", "less_than", "greater_than", "is_empty", "not_empty", "is_true", "is_false", "is_null"],
}

_LABEL = {
    "equals": "equals", "not_equals": "does not equal", "less_than": "is less than", "greater_than": "is greater than",
    "is_2xx": "is 2xx", "is_3xx": "is 3xx", "is_4xx": "is 4xx", "is_5xx": "is 5xx", "in": "is one of",
    "exists": "exists", "not_exists": "does not exist", "contains": "contains", "not_contains": "does not contain",
    "matches": "matches", "is_json": "is valid JSON", "is_empty": "is empty", "not_empty": "is not empty",
    "type_is": "is of type", "length_equals": "has length", "is_true": "is true", "is_false": "is false", "is_null": "is null",
}


def describe(t: dict[str, Any]) -> str:
    """Human name for a test, like Postman's "Status code is 200"."""
    kind, op, target, value = t.get("type", ""), t.get("op", ""), t.get("target", ""), t.get("value", "")
    label = _LABEL.get(op, op)
    subject = {"status": "Status code", "time": "Response time (ms)", "header": f"Header {target}",
               "body": "Body", "json": f"JSON {target}"}.get(kind, kind)
    needs_value = op not in ("exists", "not_exists", "is_json", "is_empty", "not_empty", "is_2xx", "is_3xx",
                             "is_4xx", "is_5xx", "is_true", "is_false", "is_null")
    return f"{subject} {label}{' ' + str(value) if needs_value and value != '' else ''}".strip()


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_text(v: Any) -> str:
    return v if isinstance(v, str) else to_text(v)


def _json_type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _loose_equal(actual: Any, expected: str) -> bool:
    if isinstance(actual, bool):
        return expected.strip().lower() in (("true", "1") if actual else ("false", "0"))
    if actual is None:
        return expected.strip().lower() in ("null", "none", "")
    if isinstance(actual, (int, float)):
        n = _num(expected)
        return n is not None and float(actual) == n
    if isinstance(actual, (dict, list)):
        try:
            return actual == json.loads(expected)
        except ValueError:
            return json.dumps(actual, sort_keys=True) == expected
    return str(actual) == expected


def _compare(op: str, actual: Any, expected: str) -> tuple[bool, str]:
    """Shared operators for json/header/body values. Returns (passed, note)."""
    shown = _as_text(actual)
    if len(shown) > 120:
        shown = shown[:117] + "…"
    if op == "equals":
        return _loose_equal(actual, expected), f"actual: {shown}"
    if op == "not_equals":
        return not _loose_equal(actual, expected), f"actual: {shown}"
    if op == "contains":
        if isinstance(actual, list):
            return any(_loose_equal(x, expected) for x in actual), f"{len(actual)} item(s)"
        if isinstance(actual, dict):
            return expected in actual, f"keys: {', '.join(list(actual)[:10])}"
        return expected in _as_text(actual), f"actual: {shown}"
    if op == "not_contains":
        ok, note = _compare("contains", actual, expected)
        return not ok, note
    if op == "matches":
        try:
            return re.search(expected, _as_text(actual)) is not None, f"actual: {shown}"
        except re.error as exc:
            return False, f"invalid regex: {exc}"
    if op in ("less_than", "greater_than"):
        a, e = _num(actual), _num(expected)
        if a is None or e is None:
            return False, f"not a number: {shown}"
        return (a < e) if op == "less_than" else (a > e), f"actual: {shown}"
    if op == "is_empty":
        return actual in ("", None, [], {}), f"actual: {shown}"
    if op == "not_empty":
        return actual not in ("", None, [], {}), f"actual: {shown}"
    if op == "type_is":
        return _json_type(actual) == expected.strip().lower(), f"actual type: {_json_type(actual)}"
    if op == "length_equals":
        n = len(actual) if isinstance(actual, (str, list, dict)) else None
        e = _num(expected)
        return n is not None and e is not None and n == int(e), f"actual length: {n}"
    if op == "is_true":
        return actual is True, f"actual: {shown}"
    if op == "is_false":
        return actual is False, f"actual: {shown}"
    if op == "is_null":
        return actual is None, f"actual: {shown}"
    return False, f"unknown operator {op}"


def _header(result: dict[str, Any], name: str) -> str | None:
    resp = result.get("response") or {}
    for k, v in resp.get("headers", []):
        if k.lower() == name.strip().lower():
            return v
    return None


def _json_value(result: dict[str, Any], path: str) -> Any:
    data = result.get("_json")
    if data is None and (result.get("response") or {}).get("body_text"):
        try:
            data = json.loads(result["response"]["body_text"])
        except ValueError as exc:
            raise _Missing("body is not JSON") from exc
    path = (path or "").strip()
    if path in ("", "$", "."):
        return data
    if path.startswith("$."):
        path = path[2:]
    return _walk(data, path)


def run_tests(tests: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tests or []:
        if not t.get("enabled", True):
            continue
        name = (t.get("name") or "").strip() or describe(t)
        try:
            passed, note = _evaluate(t, result)
        except _Missing as exc:
            passed, note = False, f"missing: {exc}"
        except Exception as exc:  # noqa: BLE001 - a broken test must not break the run
            passed, note = False, f"{type(exc).__name__}: {exc}"
        out.append({"name": name, "passed": bool(passed), "note": note})
    return out


def _evaluate(t: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
    kind, op = t.get("type"), t.get("op") or ""
    expected = str(t.get("value", "") if t.get("value") is not None else "")
    if kind == "status":
        code = result.get("status_code")
        if code is None:
            return False, f"no response: {result.get('error') or 'error'}"
        note = f"actual: {code}"
        if op == "equals":
            return str(code) == expected.strip(), note
        if op == "not_equals":
            return str(code) != expected.strip(), note
        if op == "less_than":
            return (_num(expected) is not None) and code < _num(expected), note
        if op == "greater_than":
            return (_num(expected) is not None) and code > _num(expected), note
        if op in ("is_2xx", "is_3xx", "is_4xx", "is_5xx"):
            return code // 100 == int(op[3]), note
        if op == "in":
            wanted = [s.strip() for s in re.split(r"[,\s]+", expected) if s.strip()]
            return str(code) in wanted, note
        return False, f"unknown operator {op}"
    if kind == "time":
        ms = result.get("duration_ms") or 0
        e = _num(expected)
        note = f"actual: {ms:.0f} ms"
        if e is None:
            return False, "expected value is not a number"
        return (ms < e) if op == "less_than" else (ms > e), note
    if kind == "header":
        if result.get("response") is None:
            return False, f"no response: {result.get('error') or 'error'}"
        value = _header(result, t.get("target") or "")
        if op == "exists":
            return value is not None, "present" if value is not None else "absent"
        if op == "not_exists":
            return value is None, "present" if value is not None else "absent"
        if value is None:
            return False, "header absent"
        return _compare(op, value, expected)
    if kind == "body":
        if result.get("response") is None:
            return False, f"no response: {result.get('error') or 'error'}"
        text = result["response"].get("body_text") or ""
        if op == "is_json":
            try:
                json.loads(text)
                return True, "valid JSON"
            except ValueError as exc:
                return False, f"invalid JSON: {exc}"
        return _compare(op, text, expected)
    if kind == "json":
        if result.get("response") is None:
            return False, f"no response: {result.get('error') or 'error'}"
        path = t.get("target") or ""
        try:
            value = _json_value(result, path)
        except _Missing:
            if op == "not_exists":
                return True, "absent"
            return False, f"path not found: {path or '$'}"
        if op == "exists":
            return True, "present"
        if op == "not_exists":
            return False, "present"
        return _compare(op, value, expected)
    return False, f"unknown test type {kind}"


def extract(rules: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate extraction rules. Returns [{name, scope, value, ok, note}] without saving anything."""
    out: list[dict[str, Any]] = []
    for r in rules or []:
        if not r.get("enabled", True):
            continue
        name = (r.get("name") or "").strip()
        if not name:
            continue
        scope = r.get("scope") or "env"
        src, path = r.get("source") or "json", r.get("path") or ""
        try:
            if src == "status":
                value: Any = result.get("status_code")
                if value is None:
                    raise _Missing("no response")
            elif src == "header":
                value = _header(result, path)
                if value is None:
                    raise _Missing(f"header {path}")
            elif src == "body":
                value = (result.get("response") or {}).get("body_text")
                if value is None:
                    raise _Missing("no response")
            elif src == "regex":
                text = (result.get("response") or {}).get("body_text") or ""
                m = re.search(path, text)
                if not m:
                    raise _Missing(f"no match for {path}")
                value = m.group(1) if m.groups() else m.group(0)
            else:
                value = _json_value(result, path)
            out.append({"name": name, "scope": scope, "value": _as_text(value), "ok": True,
                        "note": f"{src} {path}".strip()})
        except _Missing as exc:
            out.append({"name": name, "scope": scope, "value": None, "ok": False, "note": f"not found: {exc}"})
        except re.error as exc:
            out.append({"name": name, "scope": scope, "value": None, "ok": False, "note": f"invalid regex: {exc}"})
    return out
