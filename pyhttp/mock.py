"""Mock servers served from pyhttp's own port.

A mock server is a name, a slug and a list of routes. Everything under
`/mock/<slug>/…` on the port pyhttp already listens on is answered from those
routes, so there is no second process and no second port to remember:

    http://127.0.0.1:8765/mock/payments/users/42

A route path may hold parameters written either way (`/users/:id` or
`/users/{id}`); `*` matches one segment and `**` matches the rest of the path
(as `{{req.params.rest}}`).
The response body is a `{{ ... }}` template, so besides variables and the
dynamic values (`{{$guid}}`, `{{$randomEmail}}`…) it can read the incoming
request:

    {{req.params.id}}       a path parameter (from `:id` / `{id}` in the route path)
    {{req.query.limit}}     a query-string value
    {{req.headers.x-key}}   a request header (case-insensitive)
    {{req.body}}            the raw request body
    {{req.json.user.name}}  a path into the JSON request body
    {{req.method}} {{req.path}}
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl

from . import db
from .templating import build_context, render

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
MAX_LOGGED_BODY = 4000   # a logged call keeps at most this much of the request body
_PARAM = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\}")


def normalize_path(path: str) -> str:
    """`users/:id/` -> `/users/:id` so routes and incoming paths compare equal."""
    path = (path or "").strip()
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def compile_path(path: str) -> tuple[re.Pattern[str], int]:
    """Regex for a route path, plus how many wildcard segments it has (lower = more specific)."""
    parts: list[str] = []
    wild = 0
    for seg in normalize_path(path).strip("/").split("/"):
        if not seg:
            continue
        if seg == "**":
            parts.append("(?P<rest>.*)")
            wild += 100
            break
        if seg == "*":
            parts.append("[^/]+")
            wild += 1
            continue
        m = _PARAM.fullmatch(seg)
        if m:
            parts.append(f"(?P<{m.group(1) or m.group(2)}>[^/]+)")
            wild += 1
        else:
            parts.append(re.escape(seg))
    pattern = "/" + "/".join(parts) if parts else "/"
    return re.compile(f"^{pattern}/?$"), wild


def _query_conditions(match_query: str) -> list[tuple[str, str]]:
    """`status=error&kind=card` -> the pairs an incoming query must contain."""
    return [(k, v) for k, v in parse_qsl(match_query.lstrip("?"), keep_blank_values=True)]


def match_route(routes: list[dict[str, Any]], method: str, path: str,
                query: dict[str, str]) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The best route for a request: exact paths beat parameters, and a route with
    query conditions beats a plain one. Ties go to the earlier route."""
    method, path = method.upper(), normalize_path(path)
    best: tuple[tuple[int, int, int], dict[str, Any], dict[str, str]] | None = None
    for pos, route in enumerate(routes):
        if not route.get("enabled", True):
            continue
        route_method = str(route.get("method") or "GET").upper()
        if route_method not in ("ANY", "*") and route_method != method:
            continue
        rx, wild = compile_path(route.get("path") or "/")
        m = rx.match(path)
        if not m:
            continue
        conditions = _query_conditions(route.get("match_query") or "")
        if any(query.get(k) != v for k, v in conditions):
            continue
        rank = (wild, -len(conditions), pos)   # smaller wins
        if best is None or rank < best[0]:
            best = (rank, route, {k: v for k, v in m.groupdict().items() if v is not None})
    if best is None and method == "HEAD":
        return match_route(routes, "GET", path, query)   # HEAD is a GET without the body
    return (best[1], best[2]) if best else (None, {})


def request_context(method: str, path: str, query: dict[str, str], headers: dict[str, str],
                    body: str, params: dict[str, str]) -> dict[str, Any]:
    """Template context for a mock response: variables plus a `req` object."""
    parsed: Any = {}
    if body:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {}
    variables, _env = db.effective_variables()
    ctx = build_context(variables)
    ctx["req"] = {"method": method.upper(), "path": normalize_path(path), "params": params,
                  "query": dict(query), "headers": dict(headers), "body": body, "json": parsed}
    return ctx


def cors_headers(origin: str = "*", request_headers: str = "") -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": ", ".join(METHODS),
        "Access-Control-Allow-Headers": request_headers or "*",
        "Access-Control-Max-Age": "600",
    }


def build_response(server: dict[str, Any], route: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Status, headers and rendered body for a matched route."""
    body = render(route.get("body") or "", ctx)
    headers = {"Content-Type": route.get("content_type") or "application/json"}
    for row in route.get("headers") or []:
        if row.get("enabled", True) and str(row.get("key") or "").strip():
            headers[str(row["key"]).strip()] = render(str(row.get("value") or ""), ctx)
    if server.get("cors", True):
        headers.update(cors_headers())
    return {"status_code": int(route.get("status_code") or 200), "headers": headers, "body": body,
            "delay_s": (float(server.get("delay_ms") or 0) + float(route.get("delay_ms") or 0)) / 1000.0}


def not_found_body(server: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    """The answer when nothing matches: a 404 that says what the server does know."""
    routes = server.get("routes") or []
    known = [f"{r.get('method', 'GET')} {normalize_path(r.get('path') or '/')}" for r in routes
             if r.get("enabled", True)]
    detail = (f"No mock route in \"{server['name']}\" matches {method.upper()} {normalize_path(path)}")
    body = json.dumps({"error": "no_mock_route", "detail": detail, "mock": server["name"],
                       "routes": known}, indent=2)
    headers = {"Content-Type": "application/json"}
    if server.get("cors", True):
        headers.update(cors_headers())
    return {"status_code": 404, "headers": headers, "body": body, "delay_s": 0.0}


def log_call(server: dict[str, Any], route: dict[str, Any] | None, method: str, path: str,
             query_string: str, headers: dict[str, str], body: str, status_code: int,
             duration_ms: float) -> None:
    if not server.get("log_calls", True):
        return
    db.add_mock_call(server["id"], {
        "route_id": route["id"] if route else None,
        "route_name": (route.get("name") or f"{route.get('method')} {route.get('path')}") if route else "",
        "ts": time.time(), "method": method.upper(), "path": normalize_path(path),
        "query": query_string, "status_code": status_code, "matched": bool(route),
        "duration_ms": duration_ms, "req_headers": headers, "req_body": body[:MAX_LOGGED_BODY],
    })


# --------------------------------------------------------- seeding from requests
def _path_of(url: str) -> str:
    """The path part of a saved request's URL, variables and all: `{{base}}/users/:id` -> `/users/:id`."""
    url = (url or "").strip()
    url = re.sub(r"^\s*\{\{[^}]*\}\}", "", url)            # a leading {{base_url}}
    url = re.sub(r"^[a-zA-Z][\w+.-]*://[^/]+", "", url)    # or a real scheme + host
    return normalize_path(url.split("?", 1)[0] or "/")


def routes_from_requests(request_ids: list[str]) -> list[dict[str, Any]]:
    """Draft routes for saved requests, answering with the response each last gave
    (from the request history) or an empty JSON object."""
    history = db.list_history(limit=db.MAX_HISTORY)
    last: dict[str, dict[str, Any]] = {}
    for entry in history:                                  # newest first
        rid = entry.get("request_id")
        if rid and rid not in last and entry.get("status_code"):
            last[rid] = entry
    drafts = []
    for rid in request_ids:
        req = db.get_request(rid)
        if not req:
            continue
        seen = last.get(rid)
        full = db.get_history(seen["id"]) if seen else None
        resp = ((full or {}).get("detail") or {}).get("response") or {}
        body = resp.get("body_text") or ""
        status = int(resp.get("status_code") or 200)
        content_type = next((v for k, v in (resp.get("headers") or []) if str(k).lower() == "content-type"),
                            "application/json")
        drafts.append({"name": req["name"], "method": req.get("method") or "GET", "path": _path_of(req.get("url")),
                       "status_code": status, "content_type": content_type.split(";")[0].strip(),
                       "body": body or "{}"})
    return drafts
