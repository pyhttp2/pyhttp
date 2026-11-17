"""MCP server: the collection, sending and running, exposed to AI tools.

    python -m pyhttp mcp                     # stdio transport (Claude Code, Claude Desktop, Cursor)
    python -m pyhttp mcp --data-dir PATH     # another data folder

Every tool goes through the same REST API the UI uses (in process, no network),
so behavior, history and validation are identical to the app. The data folder is
shared with the app (SQLite in WAL mode), so both can be open at the same time.

Claude Code:      claude mcp add pyhttp -- python -m pyhttp mcp
Claude Desktop:   {"mcpServers": {"pyhttp": {"command": "python", "args": ["-m", "pyhttp", "mcp"]}}}
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

BODY_LIMIT = 40_000  # characters of a response body per tool result

server = MCPServer(
    "pyhttp",
    instructions="A local HTTP client (alternative to Postman). Requests live in folders, can hold several named "
    "bodies, tests and scripts, and use {{variables}} from the active environment and globals. "
    "send_request/send return the response; run executes many requests in order and reports.",
)

_client: httpx.AsyncClient | None = None
_data_dir: Path | None = None
_project: str | None = None


def configure(data_dir: Path, project: str | None = None) -> None:
    """Pick the data folder (and optionally a project by name or id; default: the active one);
    the app (and its database) starts on first use."""
    global _data_dir, _project, _client
    _data_dir = data_dir
    _project = project
    _client = None


def use_client(client: httpx.AsyncClient) -> None:
    """Send every tool's API calls through ``client`` instead of an app started on a data folder.
    pyhttp on a server uses this to serve many people from one process: its client hands each
    call to the app of the person who made the MCP request. Paths start with ``/api``."""
    global _client
    _client = client


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        if _data_dir is None:
            raise RuntimeError("configure(data_dir) was not called")
        from .app import create_app  # imported late: starting the app opens the database

        _client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(_data_dir, project=_project)), base_url="http://pyhttp")
    return _client


async def _api(method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None) -> Any:
    r = await _get_client().request(method, "/api" + path, json=json_body, params=params)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:  # noqa: BLE001
            detail = r.text
        raise RuntimeError(f"pyhttp API {r.status_code}: {detail}")
    return r.json() if "application/json" in (r.headers.get("content-type") or "") else r.text


# ---------------------------------------------------------------- helpers
def _request_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "method": r["method"], "url": r["url"],
            "tags": r.get("tags") or [], "folder_id": r.get("folder_id"),
            "bodies": [b.get("name") for b in r.get("bodies") or []]}


def _result_summary(res: dict[str, Any]) -> dict[str, Any]:
    resp = res.get("response") or {}
    body = resp.get("body_text")
    if isinstance(body, str) and len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT] + f"\n… truncated ({resp.get('size')} bytes in total)"
    out = {"status": res.get("status"), "status_code": res.get("status_code"),
           "duration_ms": res.get("duration_ms"), "url": (res.get("request") or {}).get("url"),
           "content_type": resp.get("content_type"), "size": resp.get("size"),
           "headers": dict(resp.get("headers") or []), "body": body}
    if res.get("error"):
        out["error"] = res["error"]
    if res.get("tests"):
        out["tests"] = [{"name": t.get("name"), "passed": t.get("passed"), "note": t.get("note", "")}
                        for t in res["tests"]]
    if res.get("extracted"):
        out["extracted_variables"] = res["extracted"]
    if res.get("body_name"):
        out["body_name"] = res["body_name"]
    return out


async def _folder_id(folder: str | None, create: bool = False) -> str | None:
    """A folder given as id or name -> id (optionally created at the root)."""
    if not folder:
        return None
    folders = await _api("GET", "/folders")
    for f in folders:
        if f["id"] == folder or f["name"].lower() == folder.lower():
            return f["id"]
    if create:
        made = await _api("POST", "/folders", json_body={"name": folder})
        return made["id"]
    raise RuntimeError(f"Folder not found: {folder!r} (list_folders shows what exists)")


# ------------------------------------------------------------------ tools
@server.tool()
async def list_requests(query: str = "", tag: str = "") -> list[dict[str, Any]]:
    """List saved requests (id, name, method, url, tags, folder, body names). query matches name, url,
    tags and description."""
    rows = await _api("GET", "/requests", params={"q": query, "tag": tag})
    return [_request_summary(r) for r in rows]


@server.tool()
async def get_request(request_id: str) -> dict[str, Any]:
    """The full definition of one request: params, headers, auth, bodies, tests, extractions, scripts."""
    return await _api("GET", f"/requests/{request_id}")


@server.tool()
async def create_request(name: str, url: str, method: str = "GET", folder: str | None = None,
                         params: list[dict[str, Any]] | None = None, headers: list[dict[str, Any]] | None = None,
                         body_type: str = "none", body: str = "", tags: list[str] | None = None,
                         description: str = "", tests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Create a request. params/headers rows are {"key", "value", "enabled"}. body_type: none, json, text,
    xml, html, javascript, form, multipart, binary or graphql; a non-empty body becomes the first named body.
    folder may be a folder name (created if missing) or id. tests rows are code-free assertions, e.g.
    {"type": "status", "op": "equals", "value": "200"} or {"type": "json", "target": "user.id", "op": "exists"}
    (types: status, time, header, body, json; target is the header name or JSON path)."""
    folder_id = await _folder_id(folder, create=True)
    payload: dict[str, Any] = {"name": name, "url": url, "method": method.upper(), "folder_id": folder_id,
                               "params": params or [], "headers": headers or [], "tags": tags or [],
                               "description": description, "body_type": body_type, "tests": tests or []}
    if body and body_type != "none":
        payload["bodies"] = [{"name": "Body 1", "body": body, "enabled": True}]
    made = await _api("POST", "/requests", json_body=payload)
    return _request_summary(made)


@server.tool()
async def update_request(request_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Change fields of a request. patch holds only what changes, with the same shapes get_request returns
    (e.g. {"url": "...", "method": "POST", "headers": [...], "bodies": [...], "tests": [...]})."""
    current = await _api("GET", f"/requests/{request_id}")
    current.update(patch)
    updated = await _api("PUT", f"/requests/{request_id}", json_body=current)
    return _request_summary(updated)


@server.tool()
async def delete_request(request_id: str) -> dict[str, Any]:
    """Delete a request permanently."""
    return await _api("DELETE", f"/requests/{request_id}")


@server.tool()
async def send_request(request_id: str, body_name: str | None = None) -> dict[str, Any]:
    """Send a saved request and return the response (status, headers, body, test results). Variables,
    auth, scripts and dependencies apply exactly as in the app. A request with several enabled bodies
    is sent with its first enabled body unless body_name picks one."""
    req = await _api("GET", f"/requests/{request_id}")
    if body_name is not None:
        names = [b.get("name") for b in req.get("bodies") or []]
        if body_name not in names:
            raise RuntimeError(f"No body named {body_name!r}; this request has {names}")
        req["bodies"] = [{**b, "enabled": b.get("name") == body_name} for b in req["bodies"]]
    elif req.get("bodies"):
        enabled = [b for b in req["bodies"] if b.get("enabled")]
        first = (enabled or req["bodies"])[:1]
        req["bodies"] = [{**b, "enabled": b is first[0]} for b in req["bodies"]]
    res = await _api("POST", "/send", json_body=req)
    return _result_summary(res)


@server.tool()
async def send(url: str, method: str = "GET", headers: list[dict[str, str]] | None = None,
               body: str = "", body_type: str = "json") -> dict[str, Any]:
    """Send an ad-hoc request without saving it. headers rows are {"key", "value"}. {{variables}} from
    the active environment work here too. The send is recorded in the app's history."""
    payload: dict[str, Any] = {"name": f"{method.upper()} {url}", "url": url, "method": method.upper(),
                               "headers": [{**h, "enabled": True} for h in headers or []]}
    if body:
        payload["body_type"] = body_type
        payload["bodies"] = [{"name": "Body", "body": body, "enabled": True}]
    res = await _api("POST", "/send", json_body=payload)
    return _result_summary(res)


@server.tool()
async def run(request_ids: list[str] | None = None, folder: str | None = None, tag: str = "",
              name: str = "", iterations: int = 1, parallel: bool = False,
              stop_on_error: bool = False) -> dict[str, Any]:
    """Run several requests in order (or in parallel) like the app's collection runner, wait for the end
    and return the report: per-request status codes, durations and test results. Pick the requests by ids,
    by folder (name or id), or by tag."""
    ids = list(request_ids or [])
    if folder or tag:
        rows = await _api("GET", "/requests", params={"q": "", "tag": tag})
        fid = await _folder_id(folder) if folder else None
        ids += [r["id"] for r in rows if (fid is None or r.get("folder_id") == fid) and r["id"] not in ids]
    if not ids:
        raise RuntimeError("Nothing to run: pass request_ids, folder or tag")
    started = await _api("POST", "/runs", json_body={"request_ids": ids, "name": name,
                                                     "iterations": max(1, iterations), "parallel": parallel,
                                                     "stop_on_error": stop_on_error})
    run_id = started["id"]
    for _ in range(2000):  # runs can be long; poll until the status settles
        state = await _api("GET", f"/runs/{run_id}")
        if state.get("status") != "running":
            results = [{"name": r.get("name"), "body_name": r.get("body_name"), "iteration": r.get("iteration"),
                        "status": r.get("status"), "status_code": r.get("status_code"),
                        "duration_ms": r.get("duration_ms"), "tests_failed": r.get("tests_failed", 0),
                        "error": r.get("error")} for r in state.get("results") or []]
            return {"run_id": run_id, "status": state.get("status"), "total": state.get("total"),
                    "completed": state.get("completed"), "failed": state.get("failed"),
                    "output_dir": state.get("output_dir"), "results": results}
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Run {run_id} is still going; check it in the app")


@server.tool()
async def get_history(limit: int = 20, query: str = "", status: str = "") -> list[dict[str, Any]]:
    """Recent sends, newest first (id, request name, method, url, status code, duration). status filters:
    2xx, 3xx, 4xx, 5xx or error. Use get_history_entry for a full response."""
    rows = await _api("GET", "/history", params={"limit": limit, "q": query, "status": status})
    return [{"id": h["id"], "request_name": h.get("request_name"), "method": h.get("method"),
             "url": h.get("url"), "status_code": h.get("status_code"), "duration_ms": h.get("duration_ms"),
             "ts": h.get("ts"), "body_name": h.get("body_name")} for h in rows[:limit]]


@server.tool()
async def get_history_entry(history_id: str) -> dict[str, Any]:
    """One history entry in full: the request as sent and the whole response."""
    h = await _api("GET", f"/history/{history_id}")
    res = h.get("response") or {}
    if isinstance(res.get("body"), str) and len(res["body"]) > BODY_LIMIT:
        res["body"] = res["body"][:BODY_LIMIT] + "… (truncated)"
    return h


@server.tool()
async def list_folders() -> list[dict[str, Any]]:
    """All folders (id, name, parent_id)."""
    rows = await _api("GET", "/folders")
    return [{"id": f["id"], "name": f["name"], "parent_id": f.get("parent_id")} for f in rows]


@server.tool()
async def create_folder(name: str, parent: str | None = None) -> dict[str, Any]:
    """Create a folder, optionally inside another folder (name or id)."""
    parent_id = await _folder_id(parent)
    return await _api("POST", "/folders", json_body={"name": name, "parent_id": parent_id})


@server.tool()
async def list_environments() -> dict[str, Any]:
    """All environments with their variables, and which one is active."""
    return await _api("GET", "/environments")


@server.tool()
async def set_active_environment(environment: str | None) -> dict[str, Any]:
    """Switch the active environment by name or id; null/empty switches to no environment."""
    env_id = None
    if environment:
        envs = (await _api("GET", "/environments")).get("environments") or []
        match = next((e for e in envs if e["id"] == environment or e["name"].lower() == environment.lower()), None)
        if not match:
            raise RuntimeError(f"Environment not found: {environment!r}")
        env_id = match["id"]
    return await _api("PUT", "/environments/active", json_body={"id": env_id})


@server.tool()
async def get_variables() -> dict[str, Any]:
    """The variables a send would use right now: globals overridden by the active environment."""
    return await _api("GET", "/variables/effective")


@server.tool()
async def set_global_variable(key: str, value: str) -> dict[str, Any]:
    """Set (or overwrite) one global variable. Environment variables are edited with their environment
    (list_environments shows the shapes)."""
    current = await _api("GET", "/variables")
    current[key] = value
    return await _api("PUT", "/variables", json_body={"variables": current})


@server.tool()
async def import_curl(command: str, folder: str | None = None) -> dict[str, Any]:
    """Turn a cURL command line into a saved request (method, URL, headers, body are parsed).
    folder may be a folder name (created if missing) or id."""
    folder_id = await _folder_id(folder, create=True)
    made = await _api("POST", "/import/curl", json_body={"command": command, "save": True, "folder_id": folder_id})
    return _request_summary(made)


# ------------------------------------------------------------------ mocks
def _mock_summary(m: dict[str, Any]) -> dict[str, Any]:
    return {"id": m["id"], "name": m["name"], "slug": m.get("slug"), "url": m.get("url"), "enabled": m.get("enabled"),
            "routes": [{"id": r["id"], "name": r.get("name") or "", "method": r.get("method"), "path": r.get("path"),
                        "status_code": r.get("status_code"), "enabled": r.get("enabled", True)} for r in m.get("routes") or []]}


async def _mock(mock: str) -> dict[str, Any]:
    mocks = (await _api("GET", "/mocks"))["mocks"]
    want = mock.strip().lower()
    for m in mocks:
        if want in (m["id"], str(m.get("slug") or "").lower(), str(m.get("name") or "").lower()):
            return m
    raise RuntimeError(f"No mock server named {mock!r}. There are: {', '.join(m['name'] for m in mocks) or 'none'}")


@server.tool()
async def list_mocks() -> list[dict[str, Any]]:
    """Mock servers with their routes. ``url`` is the base address a request calls: url + route path."""
    return [_mock_summary(m) for m in (await _api("GET", "/mocks"))["mocks"]]


@server.tool()
async def create_mock(name: str, slug: str = "", description: str = "", routes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Create a mock server. routes rows: {"method": "GET"|"POST"|...|"ANY", "path": "/offers",
    "status_code": 200, "content_type": "application/json", "body": "...", "name": "..."}. A route body
    is a template: {{req.json.x}}, {{req.body}}, {{req.query.x}}, {{$guid}} read the incoming call."""
    made = await _api("POST", "/mocks", json_body={"name": name, "slug": slug, "description": description, "routes": routes or []})
    return _mock_summary(made)


@server.tool()
async def add_mock_route(mock: str, path: str, method: str = "GET", body: str = "", status_code: int = 200,
                         content_type: str = "application/json", name: str = "") -> dict[str, Any]:
    """Add a route to a mock server (by name, slug or id). See create_mock for the body template."""
    m = await _mock(mock)
    route = await _api("POST", f"/mocks/{m['id']}/routes", json_body={"name": name, "method": method, "path": path, "body": body,
                                                                     "status_code": status_code, "content_type": content_type})
    return {"mock": m["name"], "url": m.get("url"), "route": {k: route.get(k) for k in ("id", "method", "path", "status_code")}}


@server.tool()
async def update_mock_route(mock: str, route_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Change a route of a mock server: patch holds only what changes (method, path, status_code,
    content_type, body, name, enabled, delay_ms)."""
    m = await _mock(mock)
    route = await _api("PATCH", f"/mocks/{m['id']}/routes/{route_id}", json_body=patch)
    return {k: route.get(k) for k in ("id", "method", "path", "status_code", "enabled")}


# ------------------------------------------------------------------ flows
FLOW_HELP = (
    "A flow graph is {\"nodes\": [...], \"edges\": [...]}. A node is {\"id\", \"type\", \"x\", \"y\", \"config\"}; an edge "
    "{\"from\": node id, \"port\": port, \"to\": node id}. Types (ports; config): start (out; trigger 'manual'), "
    "request (ok, fail; request_id), email (received, timeout; from, subject, contains, to, timeout_s), "
    "set (out; name, value, pattern, save), if (true, false; left, op, right), delay (out; seconds), log (out; message), "
    "view (submitted, timeout; data e.g. '{{prev.json.items}}', title, multiple, min, max, field, name='selected', "
    "timeout_s, html, js: shows the data and waits for the person running the flow to pick; own HTML/JS gets "
    "pyhttp.data and calls pyhttp.submit(value)), script (ok, fail; code, input, name='result': JavaScript with input, "
    "vars, prev, email, set(name, value), log(), fail(msg), return value), stop (none; result 'pass'|'fail', message). "
    "Values are {{templates}}. One start node, id 'start'.")


async def _flow(flow: str) -> dict[str, Any]:
    flows_list = (await _api("GET", "/flows"))["flows"]
    want = flow.strip().lower()
    for f in flows_list:
        if want in (f["id"], str(f.get("name") or "").lower()):
            return f
    raise RuntimeError(f"No flow named {flow!r}. There are: {', '.join(f['name'] for f in flows_list) or 'none'}")


def _flow_summary(f: dict[str, Any]) -> dict[str, Any]:
    return {"id": f["id"], "name": f["name"], "folder": f.get("folder") or "", "blocks": len((f.get("graph") or {}).get("nodes") or []),
            "last_status": f.get("last_status"), "last_run_at": f.get("last_run_at")}


@server.tool()
async def list_flows() -> list[dict[str, Any]]:
    """Flows (saved requests wired into a small program): id, name, folder, blocks, last result."""
    return [_flow_summary(f) for f in (await _api("GET", "/flows"))["flows"]]


@server.tool(description="The whole flow, graph included (by name or id). " + FLOW_HELP)
async def get_flow(flow: str) -> dict[str, Any]:
    return await _api("GET", f"/flows/{(await _flow(flow))['id']}")


@server.tool(description="Create a flow. graph may be left out (a flow with only Start). folder: 'A' or 'A/B'. " + FLOW_HELP)
async def create_flow(name: str, graph: dict[str, Any] | None = None, folder: str = "") -> dict[str, Any]:
    return await _api("POST", "/flows", json_body={"name": name, "graph": graph, "folder": folder})


@server.tool(description="Change a flow (by name or id): a new name, folder, or the whole graph. " + FLOW_HELP)
async def update_flow(flow: str, name: str | None = None, graph: dict[str, Any] | None = None, folder: str | None = None) -> dict[str, Any]:
    body = {k: v for k, v in {"name": name, "graph": graph, "folder": folder}.items() if v is not None}
    return await _api("PUT", f"/flows/{(await _flow(flow))['id']}", json_body=body)


@server.tool()
async def run_flow(flow: str, wait_seconds: float = 20) -> dict[str, Any]:
    """Run a flow (by name or id) and wait up to wait_seconds for it to end. Returns its state and log.
    A View block waits for a person to pick in the app: the state stays 'running' with 'waiting' set."""
    run = await _api("POST", "/flow-runs", json_body={"flow_id": (await _flow(flow))["id"]})
    return await get_flow_run(run["id"], wait_seconds)


@server.tool()
async def get_flow_run(run_id: str, wait_seconds: float = 0) -> dict[str, Any]:
    """A flow run's state and whole log; with wait_seconds, waits until it ends (or that long)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, min(wait_seconds, 120.0))
    after = 0
    while True:   # the server holds each call until a new line is logged (or 20 s)
        left = deadline - loop.time()
        snap = await _api("GET", f"/flow-runs/{run_id}", params={"after": after, "wait": max(0.0, min(left, 20.0))})
        after = snap.get("last", after)
        if snap["state"] != "running" or snap.get("waiting") or left <= 0:
            break
    snap = await _api("GET", f"/flow-runs/{run_id}", params={"after": 0})   # the whole log
    waiting = snap.get("waiting")
    return {"id": snap["id"], "flow": snap["name"], "state": snap["state"], "message": snap.get("message"),
            "waiting_for_pick": ({"block": waiting["node"], "title": waiting.get("title"),
                                  "items": len(waiting["data"]) if isinstance(waiting.get("data"), list) else None} if waiting else None),
            "log": [{"block": e["node"], "port": e["port"], "status": e["status"], "text": e["text"]}
                    for e in snap.get("events") or [] if e["status"] != "running"]}


def main(argv: list[str], default_data_dir: Path) -> None:
    p = argparse.ArgumentParser(prog="pyhttp mcp", description="Serve the pyhttp MCP server on stdio.")
    p.add_argument("--data-dir", type=Path, default=default_data_dir,
                   help=f"pyhttp data folder (default: {default_data_dir})")
    p.add_argument("--project", help="project (workspace) to serve, by name or id; default: the active one")
    args = p.parse_args(argv)
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep tool-call noise out of MCP logs
    configure(args.data_dir, args.project)
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover - `python -m pyhttp mcp` is the entry point
    from .__main__ import DEFAULT_DATA_DIR

    main([], DEFAULT_DATA_DIR)
