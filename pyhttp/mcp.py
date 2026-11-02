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


def configure(data_dir: Path) -> None:
    """Pick the data folder; the app (and its database) starts on first use."""
    global _data_dir, _client
    _data_dir = data_dir
    _client = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        if _data_dir is None:
            raise RuntimeError("configure(data_dir) was not called")
        from .app import create_app  # imported late: starting the app opens the database

        _client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(_data_dir)), base_url="http://pyhttp")
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


def main(argv: list[str], default_data_dir: Path) -> None:
    p = argparse.ArgumentParser(prog="pyhttp mcp", description="Serve the pyhttp MCP server on stdio.")
    p.add_argument("--data-dir", type=Path, default=default_data_dir,
                   help=f"pyhttp data folder (default: {default_data_dir})")
    args = p.parse_args(argv)
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep tool-call noise out of MCP logs
    configure(args.data_dir)
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover - `python -m pyhttp mcp` is the entry point
    from .__main__ import DEFAULT_DATA_DIR

    main([], DEFAULT_DATA_DIR)
