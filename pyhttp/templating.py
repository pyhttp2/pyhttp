"""Tiny `{{ ... }}` template engine used for variables and response chaining.

Supported expressions:
    {{ base_url }}                 -> a saved variable
    {{ vars.base_url }}            -> same, explicit
    {{ prev.status }}              -> status code of the previous request in a run
    {{ prev.body }}                -> raw body text of the previous response
    {{ prev.json.data.token }}     -> dotted path into the previous JSON response
    {{ prev.json.items[0].id }}    -> list indexes are supported
    {{ prev.headers.content-type }}-> response header (case-insensitive)
    {{ data.email }}               -> column of the current data-file row in a run
    {{ $guid }} {{ $timestamp }}   -> dynamic values (see DYNAMIC below), like Postman
    {{ mock_url }}                 -> this app's own /mock base (see BUILTINS below)

Unresolvable expressions are left untouched so they are visible in the output.
"""
from __future__ import annotations

import json
import random
import re
import string
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

_TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_SEG = re.compile(r"^([^\[\]]*)((?:\[\d+\])*)$")
_IDX = re.compile(r"\[(\d+)\]")
_FIRST_NAMES = ["Alex", "Sam", "Maria", "Chen", "Priya", "Omar", "Lena", "Ravi", "Nina", "Jonas"]
_LAST_NAMES = ["Smith", "Garcia", "Khan", "Nguyen", "Müller", "Rossi", "Silva", "Kowalski", "Okafor", "Tanaka"]


def _random_string(n: int = 10) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


# Dynamic variables. A name may take an optional argument: {{$randomInt 5 10}} / {{$randomString 16}}.
DYNAMIC: dict[str, Callable[[list[str]], Any]] = {
    "$guid": lambda a: str(uuid.uuid4()),
    "$uuid": lambda a: str(uuid.uuid4()),
    "$timestamp": lambda a: int(time.time()),
    "$isoTimestamp": lambda a: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    "$date": lambda a: datetime.now().strftime(a[0] if a else "%Y-%m-%d"),
    "$time": lambda a: datetime.now().strftime("%H:%M:%S"),
    "$randomInt": lambda a: random.randint(int(a[0]), int(a[1])) if len(a) >= 2 else random.randint(0, int(a[0]) if a else 1000),
    "$randomString": lambda a: _random_string(int(a[0]) if a else 10),
    "$randomBoolean": lambda a: random.choice([True, False]),
    "$randomEmail": lambda a: f"{_random_string(8).lower()}@example.com",
    "$randomFirstName": lambda a: random.choice(_FIRST_NAMES),
    "$randomLastName": lambda a: random.choice(_LAST_NAMES),
    "$randomFullName": lambda a: f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}",
    "$randomPhoneNumber": lambda a: "".join(random.choices(string.digits, k=10)),
    "$randomIP": lambda a: ".".join(str(random.randint(1, 254)) for _ in range(4)),
    "$randomUrl": lambda a: f"https://{_random_string(8).lower()}.example.com/{_random_string(6).lower()}",
    "$randomPrice": lambda a: f"{random.uniform(1, 999):.2f}",
    "$randomWord": lambda a: random.choice(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]),
}


class _Missing(Exception):
    pass


def _walk(obj: Any, path: str) -> Any:
    for seg in path.split("."):
        m = _SEG.match(seg)
        if not m:
            raise _Missing(path)
        key, idx = m.group(1), m.group(2)
        if key:
            if isinstance(obj, dict):
                if key in obj:
                    obj = obj[key]
                else:
                    lower = {str(k).lower(): v for k, v in obj.items()}
                    if key.lower() in lower:
                        obj = lower[key.lower()]
                    else:
                        raise _Missing(path)
            else:
                raise _Missing(path)
        for i in _IDX.findall(idx):
            if isinstance(obj, list) and int(i) < len(obj):
                obj = obj[int(i)]
            else:
                raise _Missing(path)
    return obj


def resolve(expr: str, ctx: dict[str, Any]) -> Any:
    expr = expr.strip()
    if expr.startswith("$"):
        parts = expr.split()
        fn = DYNAMIC.get(parts[0])
        if fn is None:
            raise _Missing(expr)
        try:
            return fn(parts[1:])
        except (ValueError, IndexError) as exc:
            raise _Missing(expr) from exc
    root = expr.split(".", 1)[0].split("[", 1)[0]
    if root in ctx:
        return _walk(ctx, expr)
    return _walk(ctx.get("vars", {}), expr)


def to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(text: str | None, ctx: dict[str, Any]) -> str:
    if not text or "{{" not in text:
        return text or ""

    def repl(m: re.Match[str]) -> str:
        try:
            value = resolve(m.group(1), ctx)
        except _Missing:
            return m.group(0)
        if value is None:
            return m.group(0)
        return to_text(value)

    return _TOKEN.sub(repl, text)


def unresolved(text: str | None, ctx: dict[str, Any]) -> list[str]:
    """Names of the `{{...}}` expressions in `text` that cannot be resolved."""
    out: list[str] = []
    for m in _TOKEN.finditer(text or ""):
        try:
            if resolve(m.group(1), ctx) is None:
                raise _Missing(m.group(1))
        except _Missing:
            if m.group(1).strip() not in out:
                out.append(m.group(1).strip())
    return out


# Values the app fills in once it knows them, usable like any other variable but
# never saved: `mock_url` is the base URL of the mock servers running on
# pyhttp's own port, so `{{mock_url}}/payments/users/1` works whatever the port is.
BUILTINS: dict[str, str] = {}


def build_context(variables: dict[str, str], prev: dict[str, Any] | None = None,
                  data: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {**BUILTINS, **variables}   # a saved variable of the same name wins
    return {"vars": merged, "prev": prev or {}, "data": data or {}}
