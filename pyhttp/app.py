"""FastAPI application: JSON API under /api plus the static single-page UI."""
from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (USER_AGENT, __version__, console, curl, db, mock, monitors, oauth2, openapi, postman, projects,
               runner, templating, updater)
from .templating import build_context, render

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_RELEASE_REPO = "pyhttp2/pyhttp"  # public repo whose Releases page holds the .exe/.dmg builds
try:  # written by packaging/build.py so packaged apps know where their releases live
    from ._build import GITHUB_REPO as _BUILT_REPO
except ImportError:  # pragma: no cover - source checkout
    _BUILT_REPO = ""


def github_repo() -> str:
    """owner/repo used for update checks: Settings > env PYHTTP_GITHUB_REPO > baked at build > default."""
    return (db.get_setting("update_repo") or os.environ.get("PYHTTP_GITHUB_REPO") or _BUILT_REPO
            or DEFAULT_RELEASE_REPO).strip()


class KV(BaseModel):
    key: str = ""
    value: str = ""
    enabled: bool = True
    secret: bool = False  # environment and folder variables: masked in the UI and in saved history


class BodyIn(BaseModel):
    id: str = ""
    name: str = ""
    body: str = ""
    enabled: bool = True


class RequestIn(BaseModel):
    id: str | None = None
    folder_id: str | None = None
    name: str = "Untitled"
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    method: str = "GET"
    url: str = ""
    params: list[KV] = Field(default_factory=list)
    headers: list[KV] = Field(default_factory=list)
    body_type: str = "none"
    body: str = ""
    bodies: list[BodyIn] = Field(default_factory=list)
    timeout: float = 30
    verify_ssl: bool = True
    auth: dict[str, Any] = Field(default_factory=dict)
    tests: list[dict[str, Any]] = Field(default_factory=list)
    extract: list[dict[str, Any]] = Field(default_factory=list)
    pre_vars: list[dict[str, Any]] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    send_id: str | None = None  # client-chosen id so an in-flight Send can be cancelled


class RunIn(BaseModel):
    request_ids: list[str]
    name: str = ""
    stop_on_error: bool = False
    fail_on_http_error: bool = True
    fail_on_test: bool = True
    delay_ms: float = 0
    iterations: int = 1
    data: list[dict[str, Any]] = Field(default_factory=list)
    parallel: bool = False       # send everything at once instead of in order
    concurrency: int = 8         # at most this many in flight when parallel


class MonitorIn(BaseModel):
    name: str = "Monitor"
    request_ids: list[str] = Field(default_factory=list)
    interval_s: int = 300
    webhook_url: str = ""
    enabled: bool = True


class MonitorPatch(BaseModel):
    name: str | None = None
    request_ids: list[str] | None = None
    interval_s: int | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


class MockRouteIn(BaseModel):
    name: str = ""
    method: str = "GET"              # or ANY to answer every method
    path: str = "/"                  # /users/:id or /users/{id}; * one segment, ** the rest
    status_code: int = 200
    headers: list[KV] = Field(default_factory=list)
    content_type: str = "application/json"
    body: str = ""                   # a {{...}} template; {{req.…}} reads the incoming request
    delay_ms: float = 0
    match_query: str = ""            # only answer when the query holds these pairs: status=error
    enabled: bool = True


class MockRoutePatch(BaseModel):
    name: str | None = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    headers: list[KV] | None = None
    content_type: str | None = None
    body: str | None = None
    delay_ms: float | None = None
    match_query: str | None = None
    enabled: bool | None = None


class MockIn(BaseModel):
    name: str = "Mock server"
    slug: str = ""                   # base path: /mock/<slug>; derived from the name when empty
    description: str = ""
    enabled: bool = True
    delay_ms: float = 0
    cors: bool = True
    log_calls: bool = True
    routes: list[MockRouteIn] = Field(default_factory=list)


class MockPatch(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    enabled: bool | None = None
    delay_ms: float | None = None
    cors: bool | None = None
    log_calls: bool | None = None


class MockSeedIn(BaseModel):
    request_ids: list[str] = Field(default_factory=list)


class MockReorderIn(BaseModel):
    route_ids: list[str] = Field(default_factory=list)


class VariablesIn(BaseModel):
    variables: dict[str, str]


class FolderIn(BaseModel):
    name: str = "New folder"
    parent_id: str | None = None


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    move: bool = False  # parent_id is only applied when move is true (null = root)
    description: str | None = None
    auth: dict[str, Any] | None = None
    headers: list[KV] | None = None
    variables: list[KV] | None = None


class MoveIn(BaseModel):
    folder_id: str | None = None


class ReorderIn(BaseModel):
    request_ids: list[str] = Field(default_factory=list)
    folder_ids: list[str] = Field(default_factory=list)


class EnvIn(BaseModel):
    name: str = "New environment"
    variables: list[KV] = Field(default_factory=list)


class EnvPatch(BaseModel):
    name: str | None = None
    variables: list[KV] | None = None


class ActiveEnvIn(BaseModel):
    id: str | None = None


class ImportFolder(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    description: str = ""
    auth: dict[str, Any] = Field(default_factory=dict)
    headers: list[KV] = Field(default_factory=list)
    variables: list[KV] = Field(default_factory=list)


class ImportIn(BaseModel):
    folders: list[ImportFolder] = Field(default_factory=list)
    environments: list[EnvIn] = Field(default_factory=list)
    requests: list[RequestIn] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)
    replace: bool = False


class CurlIn(BaseModel):
    command: str
    folder_id: str | None = None
    save: bool = True


class CookieIn(BaseModel):
    domain: str
    name: str
    value: str = ""
    path: str = "/"
    expires: float | None = None
    secure: bool = False
    http_only: bool = False


class DataFileIn(BaseModel):
    text: str
    filename: str = ""


def _open_in_file_manager(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def parse_data_file(text: str, filename: str = "") -> list[dict[str, Any]]:
    """CSV (header row) or JSON (array of objects) variables file for the runner: one row of values per iteration."""
    text = text.lstrip("﻿")
    if filename.lower().endswith(".json") or text.lstrip().startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON variables file must be an array of objects")
        return [row for row in data if isinstance(row, dict)]
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or "").strip(): (v or "") for k, v in row.items()} for row in reader]


_SAMPLE_VALUES = {
    "email": ["alice@example.com", "bob@example.com", "carol@example.com"],
    "name": ["Alice", "Bob", "Carol"],
    "username": ["alice", "bob", "carol"],
    "password": ["Secret-1", "Secret-2", "Secret-3"],
    "token": ["token-1", "token-2", "token-3"],
}


def sample_data_file(requests: list[dict[str, Any]], fmt: str = "csv") -> dict[str, Any]:
    """A small variables file for the runner, with one column per {{variable}} the given requests use
    and no value for yet (or generic columns when they use none), and three example rows."""
    from .templating import _TOKEN  # noqa: PLC0415 - the token regex is private to the templating module

    found: list[str] = []
    defined: list[str] = []
    for r in requests:
        have = db.effective_variables(r.get("folder_id"))[0]
        for m in _TOKEN.finditer(json.dumps(r, ensure_ascii=False)):
            name = m.group(1).strip()
            if not re.fullmatch(r"[A-Za-z_][\w.-]*", name) or name.startswith(("data.", "prev.")) or name in ("body_name", "body_id"):
                continue
            if name not in found:
                found.append(name)
            if name in have and name not in defined:
                defined.append(name)
    columns = [c for c in found if c not in defined] or found or ["name", "email", "user_id"]
    rows: list[dict[str, str]] = []
    for i in range(3):
        row: dict[str, str] = {}
        for c in columns:
            base = c.lower().rsplit(".", 1)[-1]
            if base in _SAMPLE_VALUES:
                row[c] = _SAMPLE_VALUES[base][i]
            elif base == "id" or base.endswith("_id") or base in ("count", "limit", "page", "size", "offset"):
                row[c] = str(i + 1)
            else:
                row[c] = f"{c} {i + 1}"
        rows.append(row)
    if fmt == "json":
        return {"filename": "sample-variables.json", "text": json.dumps(rows, indent=2, ensure_ascii=False) + "\n", "columns": columns, "rows": rows}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return {"filename": "sample-variables.csv", "text": buf.getvalue(), "columns": columns, "rows": rows}


def _version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for part in v.lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def import_collection(folders: list[dict[str, Any]], requests: list[dict[str, Any]], environments: list[dict[str, Any]],
                      variables: dict[str, str], replace: bool) -> dict[str, Any]:
    """Create folders (parents first), requests and environments; optionally replace everything."""
    if replace:
        for r in db.list_requests():
            db.delete_request(r["id"])
        for f in db.list_folders():
            if not f["parent_id"]:
                db.delete_folder(f["id"])
        for e in db.list_environments():
            db.delete_environment(e["id"])
    # folders: create parents before children, remapping ids
    id_map: dict[str, str] = {}
    pending = list(folders)
    while pending:
        progressed = False
        for f in list(pending):
            if f.get("parent_id") and f["parent_id"] not in id_map and any(p["id"] == f["parent_id"] for p in pending):
                continue
            created_f = db.create_folder(f["name"], id_map.get(f.get("parent_id") or ""))
            id_map[f["id"]] = created_f["id"]
            db.update_folder(created_f["id"], description=f.get("description", ""), auth=f.get("auth") or {},
                             headers=f.get("headers") or [], variables=f.get("variables") or [])
            pending.remove(f)
            progressed = True
        if not progressed:  # cycle in the file: put the rest at the root
            for f in pending:
                id_map[f["id"]] = db.create_folder(f["name"])["id"]
            break
    created = []
    for r in requests:
        data = dict(r)
        data["folder_id"] = id_map.get(r.get("folder_id") or "")
        created.append(db.create_request(data))
    for e in environments:
        db.create_environment(e["name"], e.get("variables") or [])
    if variables:
        merged = {} if replace else db.get_variables()
        merged.update(variables)
        db.set_variables(merged)
    return {"imported": len(created), "folders": len(id_map), "environments": len(environments)}


def _own_origin(request: Request) -> str:
    """The origin this app is actually reachable at, taken from the socket we are
    bound to rather than the Host header, so mock URLs are always the real ones."""
    server = request.scope.get("server") or ("127.0.0.1", 0)
    host = "127.0.0.1" if str(server[0]) in ("0.0.0.0", "::", "") else str(server[0])
    port = int(server[1] or 0)
    default = 443 if request.url.scheme == "https" else 80
    return f"{request.url.scheme}://{host}" + ("" if port in (0, default) else f":{port}")


def _mock_route_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and tidy a mock route coming from the API."""
    if "method" in data:
        method = str(data["method"] or "GET").upper()
        if method not in (*mock.METHODS, "ANY", "*"):
            raise HTTPException(400, f"Unsupported method: {method}")
        data["method"] = "ANY" if method == "*" else method
    if "path" in data:
        data["path"] = mock.normalize_path(data["path"])
    if "status_code" in data and not 100 <= int(data["status_code"]) <= 599:
        raise HTTPException(400, "The status code must be between 100 and 599")
    if "delay_ms" in data and float(data["delay_ms"]) < 0:
        raise HTTPException(400, "The delay cannot be negative")
    return data


def create_app(data_dir: Path, project: str | None = None) -> FastAPI:
    """``project`` (an id or a name) opens that project instead of the active one, without changing
    which one is active — the CLI uses it for ``--project``."""
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    registry = projects.Registry(data_dir)
    if project:
        found = registry.find(project)
        if found is None:
            raise ValueError(f"No project called “{project}” (projects: {', '.join(p['name'] for p in registry.list())})")
        project_id = found["id"]
    else:
        project_id = registry.active()
    db_path, output_dir = registry.paths(project_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    db.init(db_path)
    db.mark_interrupted_runs()
    manager = runner.RunManager(output_dir)
    scheduler = monitors.MonitorScheduler()
    update_cache: dict[str, Any] = {}
    self_updater = updater.Updater(data_dir)
    current = {"id": project_id}   # the project this app instance has open

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="pyhttp", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

    def current_project() -> dict[str, Any]:
        return registry.get(current["id"]) or {"id": current["id"], "name": current["id"]}

    def open_project(pid: str) -> None:
        """Switch this app to another project's database and output folder."""
        new_db, new_out = registry.paths(pid)
        new_out.mkdir(parents=True, exist_ok=True)
        db.switch(new_db)
        db.mark_interrupted_runs()
        manager.output_root = new_out
        oauth2.forget()   # tokens belong to the project they were fetched in
        current["id"] = pid

    # ------------------------------------------------------------------ meta
    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {"version": __version__, "data_dir": str(data_dir), "output_dir": str(manager.output_root),
                "project": current_project(),
                "github_repo": github_repo(), "frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform,
                "install": updater.info(), "user_agent": USER_AGENT, "python": sys.executable}

    # -------------------------------------------------------------- projects
    # A project is a separate workspace (its own database and output folder, see projects.py).
    # Switching reopens the database under this process, so the UI reloads afterwards.
    def projects_payload() -> dict[str, Any]:
        return {"current": current["id"], "active": registry.active(), "projects": registry.list()}

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        return projects_payload()

    @app.post("/api/projects")
    def create_project(body: dict[str, Any]) -> dict[str, Any]:
        """Create a project (app settings are copied in so it does not start bare) and switch to it,
        unless ``switch`` is false."""
        try:
            p = registry.create(str(body.get("name") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        settings = db.app_settings_rows() if body.get("copy_settings", True) else None
        db.seed_database(registry.paths(p["id"])[0], settings)
        if body.get("switch", True):
            return activate_project(p["id"])
        return {**projects_payload(), "created": p}

    @app.post("/api/projects/{pid}/activate")
    def activate_project(pid: str) -> dict[str, Any]:
        if registry.get(pid) is None:
            raise HTTPException(404, "Project not found")
        if manager.any_active():
            raise HTTPException(409, "A run is still going. Wait for it to finish or cancel it, then switch.")
        if pid != current["id"]:
            open_project(pid)
        registry.set_active(pid)
        return projects_payload()

    @app.patch("/api/projects/{pid}")
    def rename_project(pid: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            registry.rename(pid, str(body.get("name") or ""))
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return projects_payload()

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str) -> dict[str, Any]:
        if pid == current["id"]:
            raise HTTPException(409, "Switch to another project before deleting this one")
        try:
            registry.delete(pid)
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return projects_payload()

    @app.get("/api/update-check")
    async def update_check(force: bool = Query(False)) -> dict[str, Any]:
        """Latest GitHub release, compared with the running version (cached for an hour)."""
        repo = github_repo()
        if not repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            return {"available": False, "reason": "no GitHub repository configured (Settings > About)", "current": __version__}
        if not force and update_cache.get("at", 0) > time.time() - 3600 and update_cache.get("repo") == repo:
            return update_cache["data"]
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
            if r.status_code != 200:
                data = {"available": False, "reason": f"GitHub answered {r.status_code}", "current": __version__}
            else:
                rel = r.json()
                latest = str(rel.get("tag_name") or rel.get("name") or "")
                assets = [{"name": a.get("name"), "url": a.get("browser_download_url"), "size": a.get("size")} for a in rel.get("assets") or []]
                data = {"available": _version_tuple(latest) > _version_tuple(__version__), "current": __version__,
                        "latest": latest.lstrip("vV"), "url": rel.get("html_url"), "notes": (rel.get("body") or "")[:4000],
                        "asset": updater.pick_asset(assets), "assets": assets, "published_at": rel.get("published_at"),
                        "install": updater.info(latest.lstrip("vV"))}
        except Exception as exc:  # noqa: BLE001 - offline is fine
            data = {"available": False, "reason": f"{type(exc).__name__}: {exc}", "current": __version__}
        update_cache.update({"at": time.time(), "data": data, "repo": repo})
        return data

    @app.post("/api/update/install")
    def update_install() -> dict[str, Any]:
        """Apply the update found by /api/update-check: download and install (Windows) or
        `brew upgrade` (macOS). Progress is polled from /api/update/status; the app restarts at the end."""
        data = update_cache.get("data") or {}
        if not data.get("available"):
            raise HTTPException(400, "No update is available; check for updates first.")
        try:
            return self_updater.start(data)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/update/status")
    def update_status() -> dict[str, Any]:
        return self_updater.status()

    # -------------------------------------------------------------- requests
    @app.get("/api/requests")
    def list_requests(q: str = Query(""), tag: str = Query("")) -> list[dict[str, Any]]:
        return db.list_requests(q, tag)

    @app.post("/api/requests", status_code=201)
    def create_request(body: RequestIn) -> dict[str, Any]:
        return db.create_request(body.model_dump())

    @app.get("/api/requests/{rid}")
    def get_request(rid: str) -> dict[str, Any]:
        r = db.get_request(rid)
        if not r:
            raise HTTPException(404, "Request not found")
        return r

    @app.put("/api/requests/{rid}")
    def update_request(rid: str, body: RequestIn) -> dict[str, Any]:
        r = db.update_request(rid, body.model_dump())
        if not r:
            raise HTTPException(404, "Request not found")
        return r

    @app.get("/api/requests/{rid}/dependency")
    def dependency_status(rid: str) -> dict[str, Any]:
        """When the request last ran as a dependency of another request."""
        last = db.dependency_ran(rid)
        if not last:
            return {"ran_at": None}
        return {**last, "age": round(time.time() - last["ran_at"])}

    @app.delete("/api/requests/{rid}/dependency")
    def dependency_reset(rid: str) -> dict[str, Any]:
        """Forget the last run so the request is sent again before the next dependent send."""
        db.clear_dependency_ran(rid)
        return {"ok": True}

    @app.delete("/api/requests/{rid}")
    def delete_request(rid: str) -> dict[str, Any]:
        if not db.delete_request(rid):
            raise HTTPException(404, "Request not found")
        return {"ok": True}

    @app.post("/api/requests/{rid}/duplicate", status_code=201)
    def duplicate_request(rid: str) -> dict[str, Any]:
        r = db.get_request(rid)
        if not r:
            raise HTTPException(404, "Request not found")
        r = dict(r)
        r["name"] = f"{r['name']} (copy)"
        return db.create_request(r)

    @app.post("/api/requests/{rid}/move")
    def move_request(rid: str, body: MoveIn) -> dict[str, Any]:
        try:
            r = db.move_request(rid, body.folder_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not r:
            raise HTTPException(404, "Request not found")
        return r

    @app.post("/api/reorder")
    def reorder(body: ReorderIn) -> dict[str, Any]:
        db.reorder(body.request_ids, body.folder_ids)
        return {"ok": True}

    @app.get("/api/requests/{rid}/inherited")
    def request_inherited(rid: str) -> dict[str, Any]:
        r = db.get_request(rid)
        if not r:
            raise HTTPException(404, "Request not found")
        return db.inherited(r.get("folder_id"))

    @app.get("/api/folders/{fid}/inherited")
    def folder_inherited(fid: str) -> dict[str, Any]:
        return db.inherited(fid)

    # --------------------------------------------------------------- folders
    @app.get("/api/folders")
    def list_folders() -> list[dict[str, Any]]:
        return db.list_folders()

    @app.post("/api/folders", status_code=201)
    def create_folder(body: FolderIn) -> dict[str, Any]:
        try:
            return db.create_folder(body.name, body.parent_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/api/folders/{fid}")
    def update_folder(fid: str, body: FolderPatch) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if body.name is not None:
            kwargs["name"] = body.name
        if body.move:
            kwargs["parent_id"] = body.parent_id
        if body.description is not None:
            kwargs["description"] = body.description
        if body.auth is not None:
            kwargs["auth"] = body.auth
        if body.headers is not None:
            kwargs["headers"] = [h.model_dump() for h in body.headers]
        if body.variables is not None:
            kwargs["variables"] = [v.model_dump() for v in body.variables]
        try:
            f = db.update_folder(fid, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not f:
            raise HTTPException(404, "Folder not found")
        return f

    @app.get("/api/folders/{fid}/contents")
    def folder_contents(fid: str) -> dict[str, Any]:
        if not db.get_folder(fid):
            raise HTTPException(404, "Folder not found")
        return db.folder_contents(fid)

    @app.delete("/api/folders/{fid}")
    def delete_folder(fid: str) -> dict[str, Any]:
        out = db.delete_folder(fid)
        if out is None:
            raise HTTPException(404, "Folder not found")
        return out

    # ---------------------------------------------------------- environments
    def env_payload() -> dict[str, Any]:
        active = db.active_environment()
        return {"environments": db.list_environments(), "active_id": active["id"] if active else None}

    @app.get("/api/environments")
    def list_environments() -> dict[str, Any]:
        return env_payload()

    @app.post("/api/environments", status_code=201)
    def create_environment(body: EnvIn) -> dict[str, Any]:
        return db.create_environment(body.name, [v.model_dump() for v in body.variables])

    @app.put("/api/environments/active")
    def set_active_environment(body: ActiveEnvIn) -> dict[str, Any]:
        if body.id and not db.get_environment(body.id):
            raise HTTPException(404, "Environment not found")
        db.set_setting("active_env", body.id or None)
        return env_payload()

    @app.patch("/api/environments/{eid}")
    def update_environment(eid: str, body: EnvPatch) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if body.name is not None:
            kwargs["name"] = body.name
        if body.variables is not None:
            kwargs["variables"] = [v.model_dump() for v in body.variables]
        env = db.update_environment(eid, **kwargs)
        if not env:
            raise HTTPException(404, "Environment not found")
        return env

    @app.post("/api/environments/{eid}/duplicate", status_code=201)
    def duplicate_environment(eid: str) -> dict[str, Any]:
        env = db.get_environment(eid)
        if not env:
            raise HTTPException(404, "Environment not found")
        return db.create_environment(f"{env['name']} copy", env["variables"])

    @app.delete("/api/environments/{eid}")
    def delete_environment(eid: str) -> dict[str, Any]:
        if not db.delete_environment(eid):
            raise HTTPException(404, "Environment not found")
        return env_payload()

    @app.get("/api/tags")
    def tags() -> list[dict[str, Any]]:
        return db.all_tags()

    # -------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return db.app_settings()

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("theme") is not None and body["theme"] not in ("dark", "light", "system"):
            raise HTTPException(400, "theme must be dark, light or system")
        if body.get("layout") is not None and body["layout"] not in ("below", "beside"):
            raise HTTPException(400, "layout must be below or beside")
        if body.get("ui_font") is not None and body["ui_font"] not in db.UI_FONTS:
            raise HTTPException(400, "ui_font must be one of " + ", ".join(db.UI_FONTS))
        if body.get("mono_font") is not None and body["mono_font"] not in db.MONO_FONTS:
            raise HTTPException(400, "mono_font must be one of " + ", ".join(db.MONO_FONTS))
        if body.get("certs") is not None and not isinstance(body["certs"], list):
            raise HTTPException(400, "certs must be a list")
        return db.update_settings(body)

    # ------------------------------------------------------------- variables
    @app.get("/api/variables")
    def get_variables() -> dict[str, str]:
        return db.get_variables()

    @app.put("/api/variables")
    def put_variables(body: VariablesIn) -> dict[str, str]:
        return db.set_variables(body.variables)

    @app.get("/api/variables/effective")
    def effective_variables(folder_id: str = Query("")) -> dict[str, Any]:
        values, env = db.effective_variables(folder_id or None)
        return {"variables": values, "environment": env}

    # ------------------------------------------------------------------ send
    def _editor_request(body: RequestIn) -> dict[str, Any]:
        req = db.normalize_request(body.model_dump())
        req["id"] = body.id
        req["folder_id"] = body.folder_id
        if body.id and not body.folder_id:
            saved = db.get_request(body.id)
            if saved:
                req["folder_id"] = saved.get("folder_id")
        return req

    sends: dict[str, asyncio.Task[dict[str, Any]]] = {}
    # Cancel can arrive before its send has started (the UI shows Cancel as soon as it posts the
    # send): remember it, and the send stops as soon as it starts. Oldest entries are dropped.
    early_cancels: dict[str, float] = {}
    finished_sends: dict[str, float] = {}   # a cancel after the send ended is answered "not cancelled"

    def _remember(store: dict[str, float], send_id: str) -> None:
        store[send_id] = time.time()
        while len(store) > 100:
            store.pop(next(iter(store)))

    @app.post("/api/send")
    async def send(body: RequestIn) -> dict[str, Any]:
        """Send a single request straight from the editor without saving it.
        Pass send_id to be able to cancel it with POST /api/send/{send_id}/cancel."""
        req = _editor_request(body)
        item = runner.expand(req)[0]
        variables, env_name = db.effective_variables(req.get("folder_id"))
        # no await between this check and registering the task, so a cancel cannot slip in between
        if body.send_id and early_cancels.pop(body.send_id, None) is not None:
            result = runner.cancelled_result(item)
            runner.record(result, source="Send", req=item, environment=env_name)
            out = runner.public_result(result)
            out["body_name"] = item.get("body_name")
            return out
        task = asyncio.create_task(runner.execute(item, build_context(variables)))
        if body.send_id:
            sends[body.send_id] = task
        try:
            result = await task
        except asyncio.CancelledError:
            if not task.cancelled():      # the HTTP handler itself was cancelled: stop the send too
                task.cancel()
                raise
            result = runner.cancelled_result(item)
        finally:
            if body.send_id:
                sends.pop(body.send_id, None)
                _remember(finished_sends, body.send_id)
        runner.record(result, source="Send", req=item, environment=env_name)
        out = runner.public_result(result)
        out["body_name"] = item.get("body_name")
        return out

    @app.post("/api/send-stream")
    async def send_stream(body: RequestIn) -> StreamingResponse:
        """Send like /api/send, but stream the response as NDJSON events: {"type": "status", ...}
        once the headers arrive, {"type": "chunk", "text": ...} for every piece of the body, and
        {"type": "done", "result": ...} with the same shape /api/send returns. Made for Server-Sent
        Events and long chunked responses; closing the connection stops the send."""
        req = _editor_request(body)
        item = runner.expand(req)[0]
        variables, env_name = db.effective_variables(req.get("folder_id"))
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def work() -> None:
            try:
                result = await runner.execute(item, build_context(variables), on_chunk=queue.put)
                runner.record(result, source="Send", req=item, environment=env_name)
                out = runner.public_result(result)
                out["body_name"] = item.get("body_name")
                await queue.put({"type": "done", "result": out})
            finally:
                await queue.put(None)

        async def gen():
            task = asyncio.create_task(work())
            try:
                while True:
                    ev = await queue.get()
                    if ev is None:
                        break
                    yield json.dumps(ev, default=str) + "\n"
            finally:
                task.cancel()

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/send/{send_id}/cancel")
    async def cancel_send(send_id: str) -> dict[str, Any]:
        # async: runs on the event loop, never between send()'s check and its registration
        task = sends.get(send_id)
        if task is None and send_id not in finished_sends:
            _remember(early_cancels, send_id)
            return {"cancelled": True}
        if task is None:
            return {"cancelled": False}
        if task.done():
            return {"cancelled": False}
        task.cancel()
        return {"cancelled": True}

    @app.post("/api/resolve")
    def resolve(body: RequestIn, body_id: str = Query("")) -> dict[str, Any]:
        """Everything that would be sent (variables substituted, auth applied), without sending."""
        req = _editor_request(body)
        items = runner.expand(req)
        item = next((i for i in items if body_id and any(b.get("id") == body_id and b.get("name") == i.get("body_name") for b in req["bodies"])), items[0])
        variables, env_name = db.effective_variables(req.get("folder_id"))
        ctx = build_context(variables)
        for row in req.get("pre_vars") or []:  # preview only: no persistence
            if row.get("enabled", True) and row.get("key", "").strip():
                ctx["vars"][row["key"].strip()] = row.get("value", "")
        try:
            p = runner.prepare(item, ctx)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        out = runner.public_request(p)
        out["environment"] = env_name
        out["body_name"] = item.get("body_name")
        return out

    # --------------------------------------------------------------- oauth 2
    def _oauth_settings(body: RequestIn) -> dict[str, Any]:
        """The OAuth 2.0 settings a request would use (folder inheritance, variables rendered)."""
        req = _editor_request(body)
        auth = db.normalize_auth(req.get("auth"))
        if auth["type"] == "inherit" and req.get("folder_id"):
            auth = db.inherited(req["folder_id"])["auth"]
        if auth["type"] != "oauth2":
            raise HTTPException(400, "This request does not use OAuth 2.0")
        variables, _ = db.effective_variables(req.get("folder_id"))
        ctx = build_context(variables)
        return {k: (render(v, ctx) if isinstance(v, str) else v) for k, v in auth.items()}

    @app.post("/api/oauth2/status")
    def oauth_status(body: RequestIn) -> dict[str, Any]:
        return oauth2.status(_oauth_settings(body))

    @app.post("/api/oauth2/token")
    async def oauth_token(body: RequestIn) -> dict[str, Any]:
        """Get a new token now (replaces the cached one)."""
        auth = _oauth_settings(body)
        settings = db.app_settings()
        kwargs: dict[str, Any] = {"verify": db.verify_ssl_enabled(), "timeout": float(settings.get("default_timeout") or 30)}
        if (settings.get("proxy_url") or "").strip():
            kwargs["proxy"] = settings["proxy_url"].strip()
        try:
            await oauth2.fetch(auth, kwargs, force=True)
        except Exception as exc:  # noqa: BLE001 - network errors and bad token responses
            raise HTTPException(400, str(exc)) from exc
        return oauth2.status(auth)

    @app.post("/api/oauth2/forget")
    def oauth_forget(body: RequestIn) -> dict[str, Any]:
        oauth2.forget(_oauth_settings(body))
        return {"has_token": False}

    # --------------------------------------------------------------- console
    @app.get("/api/console")
    def console_entries(after: int = Query(0)) -> dict[str, Any]:
        return console.entries(after)

    @app.delete("/api/console")
    def console_clear() -> dict[str, Any]:
        console.clear()
        return {"ok": True}

    # --------------------------------------------------------------- history
    @app.get("/api/history")
    def history(q: str = Query(""), limit: int = Query(300), status: str = Query("")) -> list[dict[str, Any]]:
        return db.list_history(limit=min(max(limit, 1), 1000), q=q, status=status)

    @app.post("/api/history/{hid}/resend")
    async def history_resend(hid: str) -> dict[str, Any]:
        """Send a past request again exactly as it was (with today's variables); returns the new entry."""
        h = db.get_history(hid)
        if not h:
            raise HTTPException(404, "History entry not found")
        snap = dict(h["snapshot"])
        req = db.normalize_request(snap)
        req["id"] = h.get("request_id")
        req["folder_id"] = snap.get("folder_id")
        if req["folder_id"] and not db.get_folder(req["folder_id"]):
            req["folder_id"] = None
        item = runner.expand(req)[0]
        if h.get("body_name"):
            item["body_name"] = h["body_name"]
        variables, env_name = db.effective_variables(req.get("folder_id"))
        result = await runner.execute(item, build_context(variables))
        runner.record(result, source="Send again", req=item, environment=env_name)
        newest = db.list_history(limit=1)
        return {"id": newest[0]["id"] if newest else None, "status_code": result.get("status_code"),
                "status": result.get("status"), "error": result.get("error")}

    @app.get("/api/history/{hid}")
    def history_item(hid: str) -> dict[str, Any]:
        h = db.get_history(hid)
        if not h:
            raise HTTPException(404, "History entry not found")
        return h

    @app.delete("/api/history/{hid}")
    def history_delete(hid: str) -> dict[str, Any]:
        return {"deleted": db.delete_history(hid)}

    @app.delete("/api/history")
    def history_clear() -> dict[str, Any]:
        return {"deleted": db.delete_history()}

    # --------------------------------------------------------------- cookies
    @app.get("/api/cookies")
    def cookies() -> list[dict[str, Any]]:
        return db.list_cookies()

    @app.put("/api/cookies")
    def put_cookie(body: CookieIn) -> list[dict[str, Any]]:
        if not body.domain.strip() or not body.name.strip():
            raise HTTPException(400, "domain and name are required")
        db.upsert_cookie(body.model_dump())
        return db.list_cookies()

    @app.delete("/api/cookies")
    def delete_cookies(domain: str | None = Query(None), name: str | None = Query(None),
                       path: str | None = Query(None)) -> dict[str, Any]:
        return {"deleted": db.delete_cookies(domain, name, path)}

    @app.post("/api/send-bodies", status_code=201)
    async def send_bodies(body: RequestIn) -> dict[str, Any]:
        """Send the editor's request once per enabled body; outputs are saved like a run."""
        req = _editor_request(body)
        items = runner.expand(req)
        settings = db.app_settings()
        options = {"name": req["name"], "mode": "bodies", "request_id": body.id,
                   "stop_on_error": False, "fail_on_http_error": True, "delay_ms": 0,
                   "parallel": bool(settings.get("parallel")), "concurrency": int(settings.get("concurrency") or 8)}
        try:
            return await manager.start(items, options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs", status_code=201)
    async def start_run(body: RunIn) -> dict[str, Any]:
        options = body.model_dump(exclude={"request_ids"})
        try:
            return await manager.start_saved(body.request_ids, options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/runs/data-file")
    def run_data_file(body: DataFileIn) -> dict[str, Any]:
        """Parse a CSV / JSON variables file for the runner and return its rows."""
        try:
            rows = parse_data_file(body.text, body.filename)
        except (ValueError, csv.Error) as exc:
            raise HTTPException(400, f"Could not read variables file: {exc}") from exc
        columns: list[str] = []
        for row in rows:
            for k in row:
                if k not in columns:
                    columns.append(k)
        return {"rows": rows, "columns": columns}

    @app.get("/api/runs/sample-data")
    def run_sample_data(request_ids: str = Query(""), format: str = Query("csv")) -> dict[str, Any]:
        """A sample CSV / JSON variables file for the runner, with a column per {{variable}} the requests use."""
        ids = [i for i in request_ids.split(",") if i]
        requests = [r for r in (db.get_request(i) for i in ids) if r]
        return sample_data_file(requests, "json" if format == "json" else "csv")

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return db.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        run["active"] = manager.is_active(run_id)
        return run

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        return {"cancelled": manager.cancel(run_id)}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> dict[str, Any]:
        if manager.is_active(run_id):
            raise HTTPException(409, "Run is still active; cancel it first")
        if not db.delete_run(run_id):
            raise HTTPException(404, "Run not found")
        return {"ok": True}

    @app.get("/api/runs/{run_id}/files/{filename}")
    def run_file(run_id: str, filename: str) -> FileResponse:
        run = db.get_run(run_id, with_results=False)
        if not run:
            raise HTTPException(404, "Run not found")
        base = Path(run["output_dir"]).resolve()
        target = (base / filename).resolve()
        if target.parent != base or not target.is_file():
            raise HTTPException(404, "File not found")
        return FileResponse(target, filename=filename)

    @app.get("/api/runs/{run_id}/report")
    def run_report(run_id: str) -> dict[str, Any]:
        """Run report: results with tests, for export as JSON or HTML in the UI."""
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        tests_total = sum(len(r.get("tests") or []) for r in run["results"])
        tests_failed = sum(1 for r in run["results"] for t in (r.get("tests") or []) if not t.get("passed"))
        return {**run, "tests_total": tests_total, "tests_failed": tests_failed, "pyhttp_version": __version__}

    @app.post("/api/runs/{run_id}/open-folder")
    def open_folder(run_id: str) -> dict[str, Any]:
        run = db.get_run(run_id, with_results=False)
        if not run:
            raise HTTPException(404, "Run not found")
        try:
            _open_in_file_manager(Path(run["output_dir"]))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Could not open folder: {exc}") from exc
        return {"ok": True, "path": run["output_dir"]}

    # ----------------------------------------------------------- monitors
    @app.get("/api/monitors")
    def list_monitors() -> dict[str, Any]:
        mons = db.list_monitors()
        for mon in mons:
            mon["recent"] = db.list_monitor_checks(mon["id"], limit=30, with_results=False)[::-1]
        return {"monitors": mons,
                "failing": sum(1 for mon in mons if mon["enabled"] and mon.get("last_status") == "fail")}

    def _monitor_payload(body: MonitorIn | MonitorPatch) -> dict[str, Any]:
        data = body.model_dump(exclude_none=True)
        if "request_ids" in data:
            missing = [i for i in data["request_ids"] if not db.get_request(i)]
            if missing:
                raise HTTPException(400, f"Unknown request ids: {', '.join(missing)}")
            if not data["request_ids"]:
                raise HTTPException(400, "Pick at least one request to monitor")
        if "interval_s" in data and data["interval_s"] < 10:
            raise HTTPException(400, "The interval must be at least 10 seconds")
        return data

    @app.post("/api/monitors", status_code=201)
    def create_monitor(body: MonitorIn) -> dict[str, Any]:
        data = _monitor_payload(body)
        if not data.get("request_ids"):
            raise HTTPException(400, "Pick at least one request to monitor")
        return db.create_monitor(data)

    @app.patch("/api/monitors/{mid}")
    def update_monitor(mid: str, body: MonitorPatch) -> dict[str, Any]:
        if not db.get_monitor(mid):
            raise HTTPException(404, "Monitor not found")
        return db.update_monitor(mid, **_monitor_payload(body))

    @app.delete("/api/monitors/{mid}")
    def delete_monitor(mid: str) -> dict[str, Any]:
        if not db.delete_monitor(mid):
            raise HTTPException(404, "Monitor not found")
        return {"ok": True}

    @app.post("/api/monitors/{mid}/run-now")
    async def run_monitor_now(mid: str) -> dict[str, Any]:
        mon = db.get_monitor(mid)
        if not mon:
            raise HTTPException(404, "Monitor not found")
        return await monitors.run_check(mon)

    @app.get("/api/monitors/{mid}/checks")
    def monitor_checks(mid: str, limit: int = Query(50)) -> list[dict[str, Any]]:
        if not db.get_monitor(mid):
            raise HTTPException(404, "Monitor not found")
        return db.list_monitor_checks(mid, limit=min(max(limit, 1), 500))

    # ------------------------------------------------------------ mock servers
    # Mocks are answered by this very app, so they live on the port pyhttp already
    # listens on: http://127.0.0.1:<port>/mock/<slug>/<route path>
    def _mock_or_404(mid: str) -> dict[str, Any]:
        m = db.get_mock(mid)
        if not m:
            raise HTTPException(404, "Mock server not found")
        return m

    def _mock_url(request: Request, slug: str) -> str:
        return f"{_own_origin(request)}/mock/{slug}"

    def _with_url(request: Request, m: dict[str, Any]) -> dict[str, Any]:
        return {**m, "base_path": f"/mock/{m['slug']}", "url": _mock_url(request, m["slug"])}

    @app.get("/api/mocks")
    def list_mocks(request: Request) -> dict[str, Any]:
        mocks = [_with_url(request, m) for m in db.list_mocks()]
        return {"mocks": mocks, "base_url": f"{_own_origin(request)}/mock",
                "enabled": sum(1 for m in mocks if m["enabled"])}

    @app.post("/api/mocks", status_code=201)
    def create_mock(body: MockIn, request: Request) -> dict[str, Any]:
        data = body.model_dump()
        if not str(data.get("name") or "").strip():
            raise HTTPException(400, "Give the mock server a name")
        return _with_url(request, db.create_mock(data))

    @app.patch("/api/mocks/{mid}")
    def update_mock(mid: str, body: MockPatch, request: Request) -> dict[str, Any]:
        _mock_or_404(mid)
        fields = body.model_dump(exclude_none=True)
        if "name" in fields and not fields["name"].strip():
            raise HTTPException(400, "Give the mock server a name")
        return _with_url(request, db.update_mock(mid, **fields))

    @app.delete("/api/mocks/{mid}")
    def delete_mock(mid: str) -> dict[str, Any]:
        if not db.delete_mock(mid):
            raise HTTPException(404, "Mock server not found")
        return {"ok": True}

    @app.post("/api/mocks/{mid}/routes", status_code=201)
    def create_mock_route(mid: str, body: MockRouteIn) -> dict[str, Any]:
        _mock_or_404(mid)
        return db.create_mock_route(mid, _mock_route_payload(body.model_dump()))

    @app.patch("/api/mocks/{mid}/routes/{rid}")
    def update_mock_route(mid: str, rid: str, body: MockRoutePatch) -> dict[str, Any]:
        _mock_or_404(mid)
        route = db.get_mock_route(rid)
        if not route or route["server_id"] != mid:
            raise HTTPException(404, "Route not found")
        return db.update_mock_route(rid, **_mock_route_payload(body.model_dump(exclude_none=True)))

    @app.delete("/api/mocks/{mid}/routes/{rid}")
    def delete_mock_route(mid: str, rid: str) -> dict[str, Any]:
        _mock_or_404(mid)
        route = db.get_mock_route(rid)
        if not route or route["server_id"] != mid:
            raise HTTPException(404, "Route not found")
        db.delete_mock_route(rid)
        return {"ok": True}

    @app.post("/api/mocks/{mid}/routes/reorder")
    def reorder_mock_routes(mid: str, body: MockReorderIn) -> dict[str, Any]:
        _mock_or_404(mid)
        db.reorder_mock_routes(mid, body.route_ids)
        return {"routes": db.list_mock_routes(mid)}

    @app.post("/api/mocks/{mid}/from-requests", status_code=201)
    def mock_from_requests(mid: str, body: MockSeedIn) -> dict[str, Any]:
        """Add a route per saved request, answering with the response it last gave."""
        _mock_or_404(mid)
        missing = [i for i in body.request_ids if not db.get_request(i)]
        if missing:
            raise HTTPException(400, f"Unknown request ids: {', '.join(missing)}")
        if not body.request_ids:
            raise HTTPException(400, "Pick at least one request")
        drafts = mock.routes_from_requests(body.request_ids)
        for draft in drafts:
            db.create_mock_route(mid, draft)
        return {"added": len(drafts), "routes": db.list_mock_routes(mid)}

    @app.get("/api/mocks/{mid}/calls")
    def mock_calls(mid: str, limit: int = Query(100)) -> list[dict[str, Any]]:
        _mock_or_404(mid)
        return db.list_mock_calls(mid, limit=min(max(limit, 1), db.MAX_MOCK_CALLS))

    @app.delete("/api/mocks/{mid}/calls")
    def clear_mock_calls(mid: str) -> dict[str, Any]:
        _mock_or_404(mid)
        return {"cleared": db.clear_mock_calls(mid)}

    @app.get("/mock", include_in_schema=False)
    def mock_root(request: Request) -> JSONResponse:
        """What is being mocked on this port, for a quick look from curl or a browser."""
        servers = [{"name": m["name"], "url": _mock_url(request, m["slug"]), "enabled": m["enabled"],
                    "routes": [f"{r['method']} {mock.normalize_path(r['path'])}" for r in m["routes"]
                               if r["enabled"]]}
                   for m in db.list_mocks()]
        return JSONResponse({"mock_servers": servers})

    @app.api_route("/mock/{slug}", methods=list(mock.METHODS), include_in_schema=False)
    @app.api_route("/mock/{slug}/{path:path}", methods=list(mock.METHODS), include_in_schema=False)
    async def mock_dispatch(slug: str, request: Request, path: str = "") -> Response:
        started = time.perf_counter()
        server = db.get_mock_by_slug(slug)
        if not server:
            raise HTTPException(404, f'No mock server answers "/mock/{slug}" (see /mock)')
        if not server["enabled"]:
            raise HTTPException(503, f'The mock server "{server["name"]}" is turned off')
        req_path = "/" + path
        query = dict(request.query_params)
        headers = {k.lower(): v for k, v in request.headers.items()}
        body = (await request.body()).decode("utf-8", errors="replace")
        route, params = mock.match_route(server["routes"], request.method, req_path, query)
        if route is None and request.method == "OPTIONS" and server["cors"]:
            # CORS preflight: answer it even without a route for OPTIONS
            return Response(status_code=204, headers=mock.cors_headers(
                headers.get("origin", "*"), headers.get("access-control-request-headers", "")))
        if route is None:
            out = mock.not_found_body(server, request.method, req_path)
        else:
            ctx = mock.request_context(request.method, req_path, query, headers, body, params)
            out = mock.build_response(server, route, ctx)
        if out["delay_s"]:
            await asyncio.sleep(min(out["delay_s"], 60))
        took = (time.perf_counter() - started) * 1000
        db.bump_mock_hits(server["id"], route["id"] if route else None)
        mock.log_call(server, route, request.method, req_path, request.url.query, headers, body,
                      out["status_code"], took)
        content = b"" if request.method == "HEAD" else out["body"]
        return Response(content=content, status_code=out["status_code"], headers=out["headers"])

    # --------------------------------------------------------- import/export
    def _export_payload() -> dict[str, Any]:
        reqs = []
        for r in db.list_requests():
            reqs.append({k: v for k, v in r.items() if k not in ("id", "created_at", "updated_at")})
        envs = [{"name": e["name"], "variables": e["variables"]} for e in db.list_environments()]
        return {"pyhttp_version": __version__, "folders": db.list_folders(), "environments": envs,
                "requests": reqs, "variables": db.get_variables()}

    @app.get("/api/export")
    def export_all() -> dict[str, Any]:
        return _export_payload()

    @app.get("/api/export/postman")
    def export_postman(folder_id: str = Query(""), name: str = Query("")) -> dict[str, Any]:
        """A Postman v2.1 collection (optionally just one folder) plus environments."""
        folders = db.list_folders()
        requests = db.list_requests()
        title = name or "pyhttp collection"
        if folder_id:
            keep = db.folder_descendants(folder_id)
            root = db.get_folder(folder_id)
            title = name or (root["name"] if root else title)
            # the exported folder becomes the collection itself: its children move to the root
            folders = [{**f, "parent_id": None} if f.get("parent_id") == folder_id else f
                       for f in folders if f["id"] in keep and f["id"] != folder_id]
            requests = [{**r, "folder_id": None} if r.get("folder_id") == folder_id else r
                        for r in requests if r.get("folder_id") in keep]
        return {"collection": postman.to_collection(title, folders, requests, db.get_variables()),
                "environments": [postman.to_environment(e) for e in db.list_environments()]}

    @app.post("/api/import")
    def import_all(body: ImportIn) -> dict[str, Any]:
        return import_collection([f.model_dump() for f in body.folders], [r.model_dump() for r in body.requests],
                          [e.model_dump() for e in body.environments], body.variables, body.replace)

    @app.post("/api/import/detect")
    def import_detect(body: dict[str, Any]) -> dict[str, Any]:
        """Tell the UI what a JSON file is: pyhttp export, Postman collection or Postman environment."""
        data = body.get("data")
        if postman.is_collection(data):
            conv = postman.from_collection(data)
            return {"kind": "postman_collection", "name": conv["name"], "requests": len(conv["requests"]),
                    "folders": len(conv["folders"])}
        if postman.is_environment(data):
            env = postman.from_environment(data)
            return {"kind": "postman_environment", "name": env["name"], "variables": len(env["variables"])}
        if openapi.is_openapi(data):
            conv = openapi.from_openapi(data)
            return {"kind": "openapi", "name": conv["name"], "requests": len(conv["requests"]),
                    "folders": len(conv["folders"]), "version": str(data.get("openapi") or data.get("swagger"))}
        if openapi.is_har(data):
            conv = openapi.from_har(data)
            return {"kind": "har", "name": conv["name"], "requests": len(conv["requests"]),
                    "folders": len(conv["folders"]), "entries": len(data["log"]["entries"])}
        if isinstance(data, dict) and ("requests" in data or "folders" in data):
            return {"kind": "pyhttp", "requests": len(data.get("requests") or []), "folders": len(data.get("folders") or []),
                    "environments": len(data.get("environments") or [])}
        if isinstance(data, list):
            return {"kind": "pyhttp", "requests": len(data), "folders": 0, "environments": 0}
        return {"kind": "unknown"}

    @app.post("/api/import/postman")
    def import_postman(body: dict[str, Any]) -> dict[str, Any]:
        data = body.get("data")
        replace = bool(body.get("replace"))
        if postman.is_collection(data):
            conv = postman.from_collection(data)
            out = import_collection(conv["folders"], conv["requests"], [], {}, replace)
            out["kind"] = "postman_collection"
            return out
        if postman.is_environment(data):
            env = postman.from_environment(data)
            db.create_environment(env["name"], env["variables"])
            return {"kind": "postman_environment", "imported": 0, "folders": 0, "environments": 1}
        for kind, test, convert in (("openapi", openapi.is_openapi, openapi.from_openapi),
                                    ("har", openapi.is_har, lambda d: openapi.from_har(d, bool(body.get("include_assets"))))):
            if test(data):
                conv = convert(data)
                out = import_collection(conv["folders"], conv["requests"], [], {}, replace)
                out["kind"] = kind
                return out
        raise HTTPException(400, "Not a Postman collection (v2.1) or environment, OpenAPI / Swagger spec or HAR file")

    @app.post("/api/import/parse")
    def import_parse(body: dict[str, Any]) -> dict[str, Any]:
        """Parse a pasted or opened file that is not JSON (OpenAPI specs are often YAML)."""
        try:
            return {"data": openapi.parse_text(str(body.get("text") or ""))}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/import/curl", status_code=201)
    def import_curl(body: CurlIn) -> dict[str, Any]:
        try:
            req = curl.parse(body.command)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not body.save:
            return req
        req["folder_id"] = body.folder_id
        return db.create_request(req)

    # ------------------------------------------------------------- utilities
    @app.get("/api/path-exists")
    def path_exists(path: str = Query("")) -> dict[str, Any]:
        p = Path(path).expanduser()
        return {"exists": p.is_file(), "size": p.stat().st_size if p.is_file() else None, "name": p.name}

    @app.post("/api/open-path")
    def open_path(body: dict[str, Any]) -> dict[str, Any]:
        p = Path(str(body.get("path") or "")).expanduser()
        if not p.exists():
            raise HTTPException(404, "Path not found")
        try:
            _open_in_file_manager(p if p.is_dir() else p.parent)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"Could not open: {exc}") from exc
        return {"ok": True}

    # -------------------------------------------------------------------- UI
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def no_stale_static(request: Any, call_next: Any) -> Any:
        # The webview caches aggressively; after an upgrade a new index.html with old
        # scripts breaks the UI. Static files always revalidate, and index.html never caches.
        # {{mock_url}} points at the mocks on this very port, whichever one we got
        templating.BUILTINS["mock_url"] = f"{_own_origin(request)}/mock"
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        # script/style URLs get ?v=<version> so every release loads fresh files
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'((?:src|href)="/static/[^"?]+)"', rf'\1?v={__version__}"', html)
        html = html.replace("<html lang=\"en\">", f"<html lang=\"en\" data-version=\"{__version__}\">", 1)
        return HTMLResponse(html)

    @app.exception_handler(ValueError)
    def value_error(_request: Any, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app
