"""SQLite storage layer.

A single connection guarded by a re-entrant lock is plenty for a local,
single-user tool and keeps the code free of ORM machinery.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    method      TEXT NOT NULL DEFAULT 'GET',
    url         TEXT NOT NULL DEFAULT '',
    params      TEXT NOT NULL DEFAULT '[]',
    headers     TEXT NOT NULL DEFAULT '[]',
    folder_id   TEXT,
    body_type   TEXT NOT NULL DEFAULT 'none',
    body        TEXT NOT NULL DEFAULT '',
    bodies      TEXT NOT NULL DEFAULT '[]',
    timeout     REAL NOT NULL DEFAULT 30,
    verify_ssl  INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS folders (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS environments (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    variables  TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS variables (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    total       INTEGER NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    options     TEXT NOT NULL DEFAULT '{}',
    output_dir  TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS run_results (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    request_id   TEXT,
    request_name TEXT NOT NULL,
    body_name    TEXT,
    method       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    status_code  INTEGER,
    duration_ms  REAL,
    error        TEXT,
    detail       TEXT NOT NULL DEFAULT '{}',
    body_file    TEXT,
    detail_file  TEXT,
    started_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_run ON run_results(run_id, position);
CREATE TABLE IF NOT EXISTS cookies (
    domain     TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT '/',
    name       TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    expires    REAL,
    secure     INTEGER NOT NULL DEFAULT 0,
    http_only  INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (domain, path, name)
);
CREATE TABLE IF NOT EXISTS history (
    id           TEXT PRIMARY KEY,
    ts           REAL NOT NULL,
    source       TEXT NOT NULL DEFAULT '',
    method       TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    request_id   TEXT,
    request_name TEXT NOT NULL DEFAULT '',
    body_name    TEXT,
    status       TEXT NOT NULL DEFAULT '',
    status_code  INTEGER,
    duration_ms  REAL,
    error        TEXT,
    environment  TEXT,
    snapshot     TEXT NOT NULL DEFAULT '{}',
    detail       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_history_ts ON history(ts);
CREATE TABLE IF NOT EXISTS dependency_runs (
    request_id  TEXT PRIMARY KEY,
    ran_at      REAL NOT NULL,
    ok          INTEGER NOT NULL DEFAULT 1,
    status_code INTEGER
);
CREATE TABLE IF NOT EXISTS monitors (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    request_ids TEXT NOT NULL DEFAULT '[]',
    interval_s  INTEGER NOT NULL DEFAULT 300,
    enabled     INTEGER NOT NULL DEFAULT 1,
    webhook_url TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    last_run_at REAL,
    last_status TEXT
);
CREATE TABLE IF NOT EXISTS monitor_checks (
    id          TEXT PRIMARY KEY,
    monitor_id  TEXT NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    ok          INTEGER NOT NULL DEFAULT 1,
    failed      INTEGER NOT NULL DEFAULT 0,
    total       INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    results     TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_monitor_checks ON monitor_checks(monitor_id, ts);
CREATE TABLE IF NOT EXISTS mock_servers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    delay_ms    REAL NOT NULL DEFAULT 0,
    cors        INTEGER NOT NULL DEFAULT 1,
    log_calls   INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mock_routes (
    id           TEXT PRIMARY KEY,
    server_id    TEXT NOT NULL REFERENCES mock_servers(id) ON DELETE CASCADE,
    name         TEXT NOT NULL DEFAULT '',
    method       TEXT NOT NULL DEFAULT 'GET',
    path         TEXT NOT NULL DEFAULT '/',
    status_code  INTEGER NOT NULL DEFAULT 200,
    headers      TEXT NOT NULL DEFAULT '[]',
    content_type TEXT NOT NULL DEFAULT 'application/json',
    body         TEXT NOT NULL DEFAULT '',
    delay_ms     REAL NOT NULL DEFAULT 0,
    match_query  TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    position     INTEGER NOT NULL DEFAULT 0,
    hits         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mock_routes ON mock_routes(server_id, position);
CREATE TABLE IF NOT EXISTS mock_calls (
    id          TEXT PRIMARY KEY,
    server_id   TEXT NOT NULL REFERENCES mock_servers(id) ON DELETE CASCADE,
    route_id    TEXT,
    route_name  TEXT NOT NULL DEFAULT '',
    ts          REAL NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    query       TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL DEFAULT 200,
    matched     INTEGER NOT NULL DEFAULT 1,
    duration_ms REAL,
    req_headers TEXT NOT NULL DEFAULT '{}',
    req_body    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mock_calls ON mock_calls(server_id, ts);
"""

_JSON_FIELDS = ("tags", "params", "headers", "bodies", "auth", "tests", "extract", "pre_vars", "options")
_FOLDER_JSON_FIELDS = ("auth", "headers", "variables")
MAX_HISTORY = 1000
_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def init(path: Path) -> None:
    global _conn
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _conn.execute("PRAGMA journal_mode = WAL")
    _conn.executescript(SCHEMA)
    _migrate(_conn)


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    def add(table: str, column: str, decl: str) -> None:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    add("requests", "bodies", "TEXT NOT NULL DEFAULT '[]'")
    add("requests", "folder_id", "TEXT")
    add("requests", "auth", "TEXT NOT NULL DEFAULT '{}'")
    add("requests", "tests", "TEXT NOT NULL DEFAULT '[]'")
    add("requests", "extract", "TEXT NOT NULL DEFAULT '[]'")
    add("requests", "pre_vars", "TEXT NOT NULL DEFAULT '[]'")
    add("requests", "options", "TEXT NOT NULL DEFAULT '{}'")
    add("requests", "position", "INTEGER NOT NULL DEFAULT 0")
    add("folders", "description", "TEXT NOT NULL DEFAULT ''")
    add("folders", "auth", "TEXT NOT NULL DEFAULT '{}'")
    add("folders", "headers", "TEXT NOT NULL DEFAULT '[]'")
    add("folders", "variables", "TEXT NOT NULL DEFAULT '[]'")
    add("folders", "position", "INTEGER NOT NULL DEFAULT 0")
    add("run_results", "body_name", "TEXT")
    add("run_results", "iteration", "INTEGER NOT NULL DEFAULT 1")
    add("run_results", "tests", "TEXT NOT NULL DEFAULT '[]'")
    conn.commit()


def _c() -> sqlite3.Connection:
    assert _conn is not None, "db.init() has not been called"
    return _conn


# ----------------------------------------------------------------- requests


def _row_to_request(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for f in _JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or ("{}" if f in ("auth", "options") else "[]"))
        except ValueError:
            d[f] = {} if f in ("auth", "options") else []
    d["verify_ssl"] = bool(d["verify_ssl"])
    d["auth"] = normalize_auth(d["auth"])          # rows from before the auth column hold {}
    d["options"] = normalize_options(d["options"])
    if not d["bodies"] and d.get("body"):
        # legacy single body -> one named variant
        d["bodies"] = [{"id": "default", "name": "Default", "body": d["body"], "enabled": True}]
    return d


AUTH_TYPES = ("none", "inherit", "basic", "bearer", "apikey", "digest", "oauth2")
BODY_TYPES = ("none", "json", "text", "xml", "html", "javascript", "form", "multipart", "binary", "graphql")


def normalize_auth(data: Any, default: str = "inherit") -> dict[str, Any]:
    d = data if isinstance(data, dict) else {}
    t = str(d.get("type") or default)
    if t not in AUTH_TYPES:
        t = default
    return {
        "type": t,
        "username": str(d.get("username") or ""),
        "password": str(d.get("password") or ""),
        "token": str(d.get("token") or ""),
        "prefix": str(d.get("prefix") if d.get("prefix") is not None else "Bearer"),
        "key": str(d.get("key") or ""),
        "value": str(d.get("value") or ""),
        "add_to": "query" if d.get("add_to") == "query" else "header",
        # OAuth 2.0 (client credentials / password grant)
        "grant": "password" if d.get("grant") == "password" else "client_credentials",
        "token_url": str(d.get("token_url") or ""),
        "client_id": str(d.get("client_id") or ""),
        "client_secret": str(d.get("client_secret") or ""),
        "scope": str(d.get("scope") or ""),
        "audience": str(d.get("audience") or ""),
        "client_auth": "body" if d.get("client_auth") == "body" else "basic",
    }


def _rules(rows: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        item = {k: ("" if r.get(k) is None else str(r.get(k))) for k in keys}
        item["enabled"] = bool(r.get("enabled", True))
        out.append(item)
    return out


def normalize_options(data: Any) -> dict[str, Any]:
    d = data if isinstance(data, dict) else {}
    out: dict[str, Any] = {}
    if d.get("follow_redirects") in (True, False):
        out["follow_redirects"] = bool(d["follow_redirects"])
    out["send_cookies"] = bool(d.get("send_cookies", True))
    out["store_cookies"] = bool(d.get("store_cookies", True))
    if d.get("stream") is True:   # show the response live as it arrives (SSE, chunked)
        out["stream"] = True
    scripts = d.get("scripts") if isinstance(d.get("scripts"), dict) else {}
    # scripts for every body, plus optional ones for a single named body (keyed by body id)
    per_body: dict[str, dict[str, str]] = {}
    for body_id, own in (scripts.get("bodies") if isinstance(scripts.get("bodies"), dict) else {}).items():
        if not isinstance(own, dict) or not str(body_id):
            continue
        pre, test = str(own.get("pre") or ""), str(own.get("test") or "")
        if pre.strip() or test.strip():
            per_body[str(body_id)] = {"pre": pre, "test": test}
    out["scripts"] = {"pre": str(scripts.get("pre") or ""), "test": str(scripts.get("test") or ""),
                      "lang": "py" if scripts.get("lang") == "py" else "js", "bodies": per_body}
    out["active_body"] = str(d.get("active_body") or "")  # body shown in the editor, remembered per request
    # a request to send first (e.g. one that fetches a token), re-sent only when its last result is older than ttl seconds
    dep = d.get("depends") if isinstance(d.get("depends"), dict) else {}
    if str(dep.get("request_id") or ""):
        try:
            ttl = max(0, int(float(dep.get("ttl") or 0)))
        except (TypeError, ValueError):
            ttl = 0
        out["depends"] = {"request_id": str(dep["request_id"]), "ttl": ttl, "retry_on_auth": bool(dep.get("retry_on_auth", True))}
    return out


def _bodies(rows: Any, legacy_body: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, r in enumerate(rows or [], start=1):
        if not isinstance(r, dict):
            continue
        bid = str(r.get("id") or "").strip() or uuid.uuid4().hex[:10]
        while bid in seen:
            bid = uuid.uuid4().hex[:10]
        seen.add(bid)
        out.append({
            "id": bid,
            "name": str(r.get("name") or "").strip() or f"Body {i}",
            "body": str(r.get("body") or ""),
            "enabled": bool(r.get("enabled", True)),
        })
    if not out and legacy_body:
        out.append({"id": "default", "name": "Default", "body": legacy_body, "enabled": True})
    return out


def _kv_rows(rows: Any) -> list[dict[str, Any]]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "key": str(r.get("key", "")),
            "value": str(r.get("value", "")),
            "enabled": bool(r.get("enabled", True)),
            **({"secret": True} if r.get("secret") else {}),
        })
    return out


def normalize_request(data: dict[str, Any]) -> dict[str, Any]:
    tags: list[str] = []
    for t in data.get("tags") or []:
        t = str(t).strip()
        if t and t not in tags:
            tags.append(t)
    try:
        timeout = float(data.get("timeout") or 30)
    except (TypeError, ValueError):
        timeout = 30.0
    bodies = _bodies(data.get("bodies"), data.get("body") or "")
    body_type = data.get("body_type") or "none"
    if body_type not in BODY_TYPES:
        body_type = "text"
    return {
        "name": (data.get("name") or "Untitled").strip() or "Untitled",
        "description": data.get("description") or "",
        "tags": tags,
        "method": (data.get("method") or "GET").upper(),
        "url": (data.get("url") or "").strip(),
        "params": _kv_rows(data.get("params")),
        "headers": _kv_rows(data.get("headers")),
        "body_type": body_type,
        "bodies": bodies,
        # `body` mirrors the first body so exports stay readable by older versions
        "body": bodies[0]["body"] if bodies else "",
        "timeout": timeout,
        "verify_ssl": bool(data.get("verify_ssl", True)),
        "auth": normalize_auth(data.get("auth")),
        "tests": _rules(data.get("tests"), ("name", "type", "target", "op", "value", "body_id")),
        "extract": _rules(data.get("extract"), ("name", "source", "path", "scope")),
        "pre_vars": _rules(data.get("pre_vars"), ("key", "value", "scope")),
        "options": normalize_options(data.get("options")),
    }


def list_requests(q: str = "", tag: str = "") -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM requests ORDER BY position, lower(name)").fetchall()
    items = [_row_to_request(r) for r in rows]
    q = (q or "").strip().lower()
    if q:
        tokens = q.split()

        def match(r: dict[str, Any]) -> bool:
            hay = " ".join(
                [r["name"], r["description"], r["url"], r["method"], " ".join(r["tags"]),
                 " ".join(b["name"] for b in r["bodies"])]
            ).lower()
            return all(tok in hay for tok in tokens)

        items = [r for r in items if match(r)]
    if tag:
        items = [r for r in items if tag in r["tags"]]
    return items


def get_request(rid: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
    return _row_to_request(row) if row else None


def get_requests(ids: list[str]) -> list[dict[str, Any]]:
    """Return requests in the same order as `ids`, skipping unknown ids."""
    found = {r["id"]: r for r in (get_request(i) for i in ids) if r}
    return [found[i] for i in ids if i in found]


def create_request(data: dict[str, Any]) -> dict[str, Any]:
    d = normalize_request(data)
    rid = uuid.uuid4().hex
    now = time.time()
    folder_id = data.get("folder_id") or None
    if folder_id and not get_folder(folder_id):
        folder_id = None
    with _lock:
        pos = _c().execute("SELECT COALESCE(MAX(position), 0) + 1 FROM requests WHERE folder_id IS ?",
                           (folder_id,)).fetchone()[0]
        _c().execute(
            """INSERT INTO requests (id, name, description, tags, method, url, params, headers,
               body_type, body, bodies, timeout, verify_ssl, created_at, updated_at, folder_id,
               auth, tests, extract, pre_vars, options, position)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, d["name"], d["description"], json.dumps(d["tags"]), d["method"], d["url"],
             json.dumps(d["params"]), json.dumps(d["headers"]), d["body_type"], d["body"],
             json.dumps(d["bodies"]), d["timeout"], int(d["verify_ssl"]), now, now, folder_id,
             json.dumps(d["auth"]), json.dumps(d["tests"]), json.dumps(d["extract"]),
             json.dumps(d["pre_vars"]), json.dumps(d["options"]), pos),
        )
        _c().commit()
    return get_request(rid)  # type: ignore[return-value]


def update_request(rid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    if not get_request(rid):
        return None
    d = normalize_request(data)
    with _lock:
        _c().execute(
            """UPDATE requests SET name=?, description=?, tags=?, method=?, url=?, params=?,
               headers=?, body_type=?, body=?, bodies=?, timeout=?, verify_ssl=?, updated_at=?,
               auth=?, tests=?, extract=?, pre_vars=?, options=?
               WHERE id=?""",
            (d["name"], d["description"], json.dumps(d["tags"]), d["method"], d["url"],
             json.dumps(d["params"]), json.dumps(d["headers"]), d["body_type"], d["body"],
             json.dumps(d["bodies"]), d["timeout"], int(d["verify_ssl"]), time.time(),
             json.dumps(d["auth"]), json.dumps(d["tests"]), json.dumps(d["extract"]),
             json.dumps(d["pre_vars"]), json.dumps(d["options"]), rid),
        )
        _c().commit()
    return get_request(rid)


def reorder(request_ids: list[str] | None = None, folder_ids: list[str] | None = None) -> None:
    """Assign positions in the given order (used for drag-to-reorder)."""
    with _lock:
        for i, rid in enumerate(request_ids or [], start=1):
            _c().execute("UPDATE requests SET position = ? WHERE id = ?", (i, rid))
        for i, fid in enumerate(folder_ids or [], start=1):
            _c().execute("UPDATE folders SET position = ? WHERE id = ?", (i, fid))
        _c().commit()


def delete_request(rid: str) -> bool:
    with _lock:
        cur = _c().execute("DELETE FROM requests WHERE id = ?", (rid,))
        _c().commit()
    return cur.rowcount > 0


def all_tags() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for r in list_requests():
        for t in r["tags"]:
            counts[t] = counts.get(t, 0) + 1
    return [
        {"tag": t, "count": n}
        for t, n in sorted(counts.items(), key=lambda kv: kv[0].lower())
    ]


def move_request(rid: str, folder_id: str | None) -> dict[str, Any] | None:
    if folder_id and not get_folder(folder_id):
        raise ValueError("Folder not found")
    with _lock:
        cur = _c().execute("UPDATE requests SET folder_id = ?, updated_at = ? WHERE id = ?",
                           (folder_id or None, time.time(), rid))
        _c().commit()
    return get_request(rid) if cur.rowcount else None


# ------------------------------------------------------------------ folders


def _row_to_folder(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for f in _FOLDER_JSON_FIELDS:
        try:
            d[f] = json.loads(d.get(f) or ("{}" if f == "auth" else "[]"))
        except ValueError:
            d[f] = {} if f == "auth" else []
    d["auth"] = normalize_auth(d["auth"])
    return d


def list_folders() -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM folders ORDER BY position, lower(name)").fetchall()
    return [_row_to_folder(r) for r in rows]


def get_folder(fid: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
    return _row_to_folder(row) if row else None


def folder_chain(fid: str | None) -> list[dict[str, Any]]:
    """Folders from the root down to `fid` (empty for a top-level request)."""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    f = get_folder(fid) if fid else None
    while f and f["id"] not in seen:
        seen.add(f["id"])
        chain.append(f)
        f = get_folder(f["parent_id"]) if f.get("parent_id") else None
    chain.reverse()
    return chain


def inherited(folder_id: str | None) -> dict[str, Any]:
    """What a request inside `folder_id` inherits: auth (nearest folder that sets one),
    headers (root to leaf, later folders override) and variables (same precedence)."""
    chain = folder_chain(folder_id)
    auth = {"type": "none"}
    auth_from = None
    headers: dict[str, dict[str, Any]] = {}
    variables: dict[str, str] = {}
    for f in chain:
        if f["auth"]["type"] not in ("inherit", "none"):
            auth, auth_from = f["auth"], f["name"]
        for h in f["headers"]:
            if h.get("enabled", True) and h.get("key", "").strip():
                headers[h["key"].strip().lower()] = {"key": h["key"].strip(), "value": h.get("value", ""), "enabled": True}
        for v in f["variables"]:
            if v.get("enabled", True) and v.get("key", "").strip():
                variables[v["key"].strip()] = v.get("value", "")
    return {"auth": normalize_auth(auth, "none"), "auth_from": auth_from,
            "headers": list(headers.values()), "variables": variables}


def folder_descendants(fid: str) -> set[str]:
    """The folder itself plus every folder nested below it."""
    children: dict[str | None, list[str]] = {}
    for f in list_folders():
        children.setdefault(f["parent_id"], []).append(f["id"])
    out, stack = set(), [fid]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children.get(cur, []))
    return out


def create_folder(name: str, parent_id: str | None = None) -> dict[str, Any]:
    name = (name or "").strip() or "New folder"
    if parent_id and not get_folder(parent_id):
        raise ValueError("Parent folder not found")
    fid = uuid.uuid4().hex
    with _lock:
        pos = _c().execute("SELECT COALESCE(MAX(position), 0) + 1 FROM folders WHERE parent_id IS ?",
                           (parent_id or None,)).fetchone()[0]
        _c().execute("INSERT INTO folders (id, name, parent_id, created_at, position) VALUES (?,?,?,?,?)",
                     (fid, name, parent_id or None, time.time(), pos))
        _c().commit()
    return get_folder(fid)  # type: ignore[return-value]


_KEEP = object()


def update_folder(fid: str, name: Any = _KEEP, parent_id: Any = _KEEP, description: Any = _KEEP,
                  auth: Any = _KEEP, headers: Any = _KEEP, variables: Any = _KEEP) -> dict[str, Any] | None:
    f = get_folder(fid)
    if not f:
        return None
    if name is not _KEEP:
        f["name"] = (name or "").strip() or f["name"]
    if parent_id is not _KEEP:
        parent_id = parent_id or None
        if parent_id and (not get_folder(parent_id) or parent_id in folder_descendants(fid)):
            raise ValueError("A folder cannot be moved into itself or its own subfolder")
        f["parent_id"] = parent_id
    if description is not _KEEP:
        f["description"] = str(description or "")
    if auth is not _KEEP:
        f["auth"] = normalize_auth(auth)
    if headers is not _KEEP:
        f["headers"] = _kv_rows(headers)
    if variables is not _KEEP:
        f["variables"] = _kv_rows(variables)
    with _lock:
        _c().execute("""UPDATE folders SET name = ?, parent_id = ?, description = ?, auth = ?, headers = ?,
                        variables = ? WHERE id = ?""",
                     (f["name"], f["parent_id"], f["description"], json.dumps(f["auth"]),
                      json.dumps(f["headers"]), json.dumps(f["variables"]), fid))
        _c().commit()
    return get_folder(fid)


def folder_contents(fid: str) -> dict[str, int]:
    ids = folder_descendants(fid)
    marks = ",".join("?" * len(ids))
    with _lock:
        n = _c().execute(f"SELECT COUNT(*) FROM requests WHERE folder_id IN ({marks})", tuple(ids)).fetchone()[0]
    return {"folders": len(ids) - 1, "requests": n}


def delete_folder(fid: str) -> dict[str, Any] | None:
    """Delete a folder with everything inside it. Returns the deleted ids."""
    if not get_folder(fid):
        return None
    ids = folder_descendants(fid)
    marks = ",".join("?" * len(ids))
    with _lock:
        req_ids = [r[0] for r in _c().execute(
            f"SELECT id FROM requests WHERE folder_id IN ({marks})", tuple(ids)).fetchall()]
        _c().execute(f"DELETE FROM requests WHERE folder_id IN ({marks})", tuple(ids))
        _c().execute(f"DELETE FROM folders WHERE id IN ({marks})", tuple(ids))
        _c().commit()
    return {"folders": sorted(ids), "requests": req_ids}


# ------------------------------------------------------------- environments


def _row_to_env(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["variables"] = json.loads(d["variables"])
    return d


def list_environments() -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM environments ORDER BY created_at").fetchall()
    return [_row_to_env(r) for r in rows]


def get_environment(eid: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM environments WHERE id = ?", (eid,)).fetchone()
    return _row_to_env(row) if row else None


def create_environment(name: str, variables: Any = None) -> dict[str, Any]:
    eid = uuid.uuid4().hex
    with _lock:
        _c().execute("INSERT INTO environments (id, name, variables, created_at) VALUES (?,?,?,?)",
                     (eid, (name or "").strip() or "New environment",
                      json.dumps(_kv_rows(variables)), time.time()))
        _c().commit()
    return get_environment(eid)  # type: ignore[return-value]


def update_environment(eid: str, name: Any = _KEEP, variables: Any = _KEEP) -> dict[str, Any] | None:
    env = get_environment(eid)
    if not env:
        return None
    if name is not _KEEP:
        env["name"] = (name or "").strip() or env["name"]
    if variables is not _KEEP:
        env["variables"] = _kv_rows(variables)
    with _lock:
        _c().execute("UPDATE environments SET name = ?, variables = ? WHERE id = ?",
                     (env["name"], json.dumps(env["variables"]), eid))
        _c().commit()
    return get_environment(eid)


def delete_environment(eid: str) -> bool:
    with _lock:
        cur = _c().execute("DELETE FROM environments WHERE id = ?", (eid,))
        _c().commit()
    if get_setting("active_env") == eid:
        set_setting("active_env", None)
    return cur.rowcount > 0


APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "theme": "dark", "verify_ssl": True, "follow_redirects": True, "max_redirects": 10,
    "proxy_url": "", "proxy_bypass": "localhost, 127.0.0.1", "user_agent": "", "default_timeout": 30,
    "certs": [], "editor_font_size": 13, "layout": "below", "update_check": True, "send_cookies": True,
    "max_history": 500, "update_repo": "",
    "ui_font": "inter", "mono_font": "default",
    "parallel": False, "concurrency": 8,   # Send ×N and runs: everything at once (bounded) instead of in order
}
UI_FONTS = ("inter", "system", "segoe", "roboto", "helvetica", "ubuntu", "verdana")   # ids match static/features.js
MONO_FONTS = ("default", "cascadia", "jetbrains", "consolas", "fira", "source", "courier")
_BOOL_SETTINGS = ("verify_ssl", "follow_redirects", "update_check", "send_cookies", "parallel")
_INT_SETTINGS = ("max_redirects", "editor_font_size", "max_history", "concurrency")
_FLOAT_SETTINGS = ("default_timeout",)
_JSON_SETTINGS = ("certs",)


def app_settings() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, default in APP_SETTINGS_DEFAULTS.items():
        raw = get_setting(key)
        if raw is None:
            out[key] = default
        elif key in _BOOL_SETTINGS:
            out[key] = raw != "0"
        elif key in _INT_SETTINGS:
            try:
                out[key] = int(float(raw))
            except ValueError:
                out[key] = default
        elif key in _FLOAT_SETTINGS:
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = default
        elif key in _JSON_SETTINGS:
            try:
                out[key] = json.loads(raw)
            except ValueError:
                out[key] = default
        else:
            out[key] = raw
    if out["theme"] not in ("dark", "light", "system"):
        out["theme"] = "dark"
    if out["layout"] not in ("below", "beside"):
        out["layout"] = "below"
    if out["ui_font"] not in UI_FONTS:
        out["ui_font"] = "inter"
    if out["mono_font"] not in MONO_FONTS:
        out["mono_font"] = "default"
    return out


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if key not in APP_SETTINGS_DEFAULTS or value is None:
            continue
        if key in _BOOL_SETTINGS:
            set_setting(key, "1" if value else "0")
        elif key in _JSON_SETTINGS:
            set_setting(key, json.dumps(value))
        else:
            set_setting(key, str(value))
    return app_settings()


def verify_ssl_enabled() -> bool:
    return get_setting("verify_ssl") != "0"


def get_setting(key: str) -> str | None:
    with _lock:
        row = _c().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    with _lock:
        _c().execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        _c().commit()


def active_environment() -> dict[str, Any] | None:
    eid = get_setting("active_env")
    return get_environment(eid) if eid else None


# ---- dependency runs: when a request last ran as a dependency of another (so a token is not fetched on every send)
def dependency_ran(request_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM dependency_runs WHERE request_id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def set_dependency_ran(request_id: str, ok: bool, status_code: int | None) -> None:
    with _lock:
        _c().execute("INSERT OR REPLACE INTO dependency_runs (request_id, ran_at, ok, status_code) VALUES (?,?,?,?)",
                     (request_id, time.time(), int(ok), status_code))
        _c().commit()


def clear_dependency_ran(request_id: str) -> None:
    with _lock:
        _c().execute("DELETE FROM dependency_runs WHERE request_id = ?", (request_id,))
        _c().commit()


def effective_variables(folder_id: str | None = None) -> tuple[dict[str, str], str | None]:
    """Global variables, overridden by folder (collection) variables, overridden by the
    enabled variables of the active environment. Same precedence as Postman."""
    values = get_variables()
    if folder_id:
        values.update(inherited(folder_id)["variables"])
    env = active_environment()
    if env:
        for row in env["variables"]:
            if row["enabled"] and row["key"].strip():
                values[row["key"].strip()] = row["value"]
    return values, (env["name"] if env else None)


def secret_values(folder_id: str | None = None) -> list[str]:
    """Values of the variables marked secret (active environment and the folder chain),
    longest first, so they can be masked in everything written to disk."""
    rows = list((active_environment() or {}).get("variables") or [])
    for f in folder_chain(folder_id) if folder_id else []:
        rows += f.get("variables") or []
    vals = {r["value"] for r in rows if r.get("secret") and len(r.get("value") or "") >= 3}
    return sorted(vals, key=len, reverse=True)


def set_variable(name: str, value: str, scope: str = "env", folder_id: str | None = None) -> str:
    """Set one variable in the active environment (or globally when scope is
    'global' or no environment is active). Scope 'collection' writes the variable
    on the request's folder (the nearest one). Returns the scope actually used."""
    name = name.strip()
    if not name:
        return scope
    if scope == "collection":
        f = get_folder(folder_id) if folder_id else None
        if f:
            rows = f["variables"]
            for row in rows:
                if row["key"].strip() == name:
                    row["value"] = value
                    row["enabled"] = True
                    break
            else:
                rows.append({"key": name, "value": value, "enabled": True})
            update_folder(f["id"], variables=rows)
            return "collection"
        scope = "global"
    env = active_environment() if scope != "global" else None
    if env:
        rows = env["variables"]
        for row in rows:
            if row["key"].strip() == name:
                row["value"] = value
                row["enabled"] = True
                break
        else:
            rows.append({"key": name, "value": value, "enabled": True})
        update_environment(env["id"], variables=rows)
        return "env"
    values = get_variables()
    values[name] = value
    set_variables(values)
    return "global"


def unset_variable(name: str, scope: str = "env", folder_id: str | None = None) -> None:
    name = name.strip()
    if not name:
        return
    if scope == "collection":
        f = get_folder(folder_id) if folder_id else None
        if f:
            update_folder(f["id"], variables=[r for r in f["variables"] if r["key"].strip() != name])
        return
    if scope == "env":
        env = active_environment()
        if env:
            update_environment(env["id"], variables=[r for r in env["variables"] if r["key"].strip() != name])
        return
    values = get_variables()
    if name in values:
        del values[name]
        set_variables(values)


# ------------------------------------------------------------------ cookies


def list_cookies() -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM cookies ORDER BY domain, path, name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["secure"], d["http_only"] = bool(d["secure"]), bool(d["http_only"])
        out.append(d)
    return out


def replace_cookies(cookies: list[dict[str, Any]]) -> None:
    now = time.time()
    with _lock:
        _c().execute("DELETE FROM cookies")
        _c().executemany(
            """INSERT OR REPLACE INTO cookies (domain, path, name, value, expires, secure, http_only, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(c["domain"], c.get("path") or "/", c["name"], c.get("value", ""), c.get("expires"),
              int(bool(c.get("secure"))), int(bool(c.get("http_only"))), now) for c in cookies],
        )
        _c().commit()


def upsert_cookie(c: dict[str, Any]) -> None:
    with _lock:
        _c().execute(
            """INSERT OR REPLACE INTO cookies (domain, path, name, value, expires, secure, http_only, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (c["domain"], c.get("path") or "/", c["name"], c.get("value", ""), c.get("expires"),
             int(bool(c.get("secure"))), int(bool(c.get("http_only"))), time.time()),
        )
        _c().commit()


def delete_cookies(domain: str | None = None, name: str | None = None, path: str | None = None) -> int:
    where, args = [], []
    if domain is not None:
        where.append("domain = ?")
        args.append(domain)
    if name is not None:
        where.append("name = ?")
        args.append(name)
    if path is not None:
        where.append("path = ?")
        args.append(path)
    sql = "DELETE FROM cookies" + (" WHERE " + " AND ".join(where) if where else "")
    with _lock:
        cur = _c().execute(sql, args)
        _c().commit()
    return cur.rowcount


# ------------------------------------------------------------------ history


def add_history(entry: dict[str, Any]) -> str:
    hid = uuid.uuid4().hex
    with _lock:
        _c().execute(
            """INSERT INTO history (id, ts, source, method, url, request_id, request_name, body_name, status,
               status_code, duration_ms, error, environment, snapshot, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (hid, entry.get("ts") or time.time(), entry.get("source", ""), entry.get("method", ""),
             entry.get("url", ""), entry.get("request_id"), entry.get("request_name", ""), entry.get("body_name"),
             entry.get("status", ""), entry.get("status_code"), entry.get("duration_ms"), entry.get("error"),
             entry.get("environment"), json.dumps(entry.get("snapshot") or {}), json.dumps(entry.get("detail") or {})),
        )
        limit = max(50, int(get_setting("max_history") or APP_SETTINGS_DEFAULTS["max_history"]))
        _c().execute(
            "DELETE FROM history WHERE id IN (SELECT id FROM history ORDER BY ts DESC LIMIT -1 OFFSET ?)", (limit,)
        )
        _c().commit()
    return hid


def _status_group(code: Any, status: str) -> str:
    if status == "cancelled":
        return "error"
    if code is None:
        return "error"
    return f"{int(code) // 100}xx"


def list_history(limit: int = 300, q: str = "", status: str = "") -> list[dict[str, Any]]:
    """Newest first. `q`: words matched against method, URL, name and status code;
    `status`: "2xx", "3xx", "4xx", "5xx" or "error" (no response)."""
    with _lock:
        rows = _c().execute(
            """SELECT id, ts, source, method, url, request_id, request_name, body_name, status, status_code,
               duration_ms, error, environment FROM history ORDER BY ts DESC LIMIT ?""", (limit,)
        ).fetchall()
    items = [dict(r) for r in rows]
    q = (q or "").strip().lower()
    if q:
        toks = q.split()
        items = [i for i in items if all(t in f"{i['method']} {i['url']} {i['request_name']} {i['status_code']}".lower() for t in toks)]
    if status:
        items = [i for i in items if _status_group(i["status_code"], i["status"]) == status]
    return items


def get_history(hid: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM history WHERE id = ?", (hid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["snapshot"] = json.loads(d["snapshot"])
    d["detail"] = json.loads(d["detail"])
    return d


def delete_history(hid: str | None = None) -> int:
    with _lock:
        cur = _c().execute("DELETE FROM history WHERE id = ?", (hid,)) if hid else _c().execute("DELETE FROM history")
        _c().commit()
    return cur.rowcount


# ---------------------------------------------------------------- variables


def get_variables() -> dict[str, str]:
    with _lock:
        rows = _c().execute("SELECT key, value FROM variables ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_variables(values: dict[str, str]) -> dict[str, str]:
    with _lock:
        _c().execute("DELETE FROM variables")
        _c().executemany(
            "INSERT OR REPLACE INTO variables (key, value) VALUES (?, ?)",
            [(k.strip(), str(v)) for k, v in values.items() if k.strip()],
        )
        _c().commit()
    return get_variables()


# --------------------------------------------------------------------- runs


def create_run(name: str, total: int, options: dict[str, Any], output_dir: str) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    with _lock:
        _c().execute(
            """INSERT INTO runs (id, name, status, total, options, output_dir, started_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, name, "running", total, json.dumps(options), output_dir, time.time()),
        )
        _c().commit()
    return get_run(run_id)  # type: ignore[return-value]


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        _c().execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))
        _c().commit()


def add_result(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    res_id = uuid.uuid4().hex
    with _lock:
        _c().execute(
            """INSERT INTO run_results (id, run_id, position, request_id, request_name, body_name,
               method, status, status_code, duration_ms, error, detail, body_file, detail_file,
               started_at, iteration, tests)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (res_id, run_id, result["position"], result.get("request_id"),
             result["request_name"], result.get("body_name"), result.get("method", ""), result["status"],
             result.get("status_code"), result.get("duration_ms"), result.get("error"),
             json.dumps(result.get("detail") or {}), result.get("body_file"),
             result.get("detail_file"), result["started_at"], result.get("iteration", 1),
             json.dumps(result.get("tests") or [])),
        )
        _c().commit()
    return {"id": res_id, **result}


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["options"] = json.loads(d["options"])
    return d


def list_runs(limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def get_run(run_id: str, with_results: bool = True) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        run = _row_to_run(row)
        if with_results:
            rows = _c().execute(
                "SELECT * FROM run_results WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["detail"] = json.loads(d["detail"])
                try:
                    d["tests"] = json.loads(d.get("tests") or "[]")
                except ValueError:
                    d["tests"] = []
                results.append(d)
            run["results"] = results
    return run


def get_result(run_id: str, result_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute(
            "SELECT * FROM run_results WHERE run_id = ? AND id = ?", (run_id, result_id)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["detail"] = json.loads(d["detail"])
    return d


def delete_run(run_id: str) -> bool:
    with _lock:
        cur = _c().execute("DELETE FROM runs WHERE id = ?", (run_id,))
        _c().commit()
    return cur.rowcount > 0


def mark_interrupted_runs() -> None:
    """Runs left in 'running' state by a previous process exit are marked interrupted."""
    with _lock:
        _c().execute(
            "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
            (time.time(),),
        )
        _c().commit()


# ----------------------------------------------------------------- monitors


def _row_to_monitor(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["request_ids"] = json.loads(d["request_ids"])
    d["enabled"] = bool(d["enabled"])
    return d


def list_monitors() -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM monitors ORDER BY created_at").fetchall()
    return [_row_to_monitor(r) for r in rows]


def get_monitor(monitor_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,)).fetchone()
    return _row_to_monitor(row) if row else None


def create_monitor(data: dict[str, Any]) -> dict[str, Any]:
    mid = uuid.uuid4().hex
    with _lock:
        _c().execute(
            "INSERT INTO monitors (id, name, request_ids, interval_s, enabled, webhook_url, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (mid, str(data.get("name") or "Monitor"), json.dumps(list(data.get("request_ids") or [])),
             max(10, int(data.get("interval_s") or 300)), 1 if data.get("enabled", True) else 0,
             str(data.get("webhook_url") or ""), time.time()),
        )
        _c().commit()
    return get_monitor(mid)  # type: ignore[return-value]


def update_monitor(monitor_id: str, **fields: Any) -> dict[str, Any] | None:
    if "request_ids" in fields:
        fields["request_ids"] = json.dumps(list(fields["request_ids"] or []))
    if "interval_s" in fields:
        fields["interval_s"] = max(10, int(fields["interval_s"]))
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with _lock:
            _c().execute(f"UPDATE monitors SET {cols} WHERE id = ?", (*fields.values(), monitor_id))
            _c().commit()
    return get_monitor(monitor_id)


def delete_monitor(monitor_id: str) -> bool:
    with _lock:
        cur = _c().execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
        _c().commit()
    return cur.rowcount > 0


def due_monitors(now: float) -> list[dict[str, Any]]:
    """Enabled monitors whose interval has passed since their last check (or that never ran)."""
    with _lock:
        rows = _c().execute(
            "SELECT * FROM monitors WHERE enabled = 1 AND (last_run_at IS NULL OR ? - last_run_at >= interval_s)",
            (now,),
        ).fetchall()
    return [_row_to_monitor(r) for r in rows]


def add_monitor_check(monitor_id: str, check: dict[str, Any], keep: int = 500) -> dict[str, Any]:
    cid = uuid.uuid4().hex
    with _lock:
        _c().execute(
            "INSERT INTO monitor_checks (id, monitor_id, ts, ok, failed, total, duration_ms, results) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, monitor_id, check["ts"], 1 if check.get("ok") else 0, int(check.get("failed") or 0),
             int(check.get("total") or 0), check.get("duration_ms"), json.dumps(check.get("results") or [])),
        )
        _c().execute(
            "DELETE FROM monitor_checks WHERE monitor_id = ? AND id NOT IN "
            "(SELECT id FROM monitor_checks WHERE monitor_id = ? ORDER BY ts DESC LIMIT ?)",
            (monitor_id, monitor_id, keep),
        )
        _c().commit()
    return {"id": cid, **check}


def list_monitor_checks(monitor_id: str, limit: int = 50, with_results: bool = True) -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM monitor_checks WHERE monitor_id = ? ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["ok"] = bool(d["ok"])
        if with_results:
            d["results"] = json.loads(d["results"])
        else:
            d.pop("results", None)
        out.append(d)
    return out


# -------------------------------------------------------------- mock servers
MAX_MOCK_CALLS = 200   # per server; older calls are dropped


def _row_to_mock(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    d["cors"] = bool(d["cors"])
    d["log_calls"] = bool(d["log_calls"])
    return d


def _row_to_mock_route(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["headers"] = json.loads(d["headers"])
    d["enabled"] = bool(d["enabled"])
    return d


def mock_slug(name: str, taken: set[str] | None = None) -> str:
    """A URL-safe slug for a mock server's base path, unique across the table."""
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "mock"
    used = taken if taken is not None else {m["slug"] for m in list_mocks(with_routes=False)}
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    return slug


def list_mocks(with_routes: bool = True) -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM mock_servers ORDER BY created_at").fetchall()
    mocks = [_row_to_mock(r) for r in rows]
    if with_routes:
        for m in mocks:
            m["routes"] = list_mock_routes(m["id"])
    return mocks


def get_mock(mock_id: str, with_routes: bool = True) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM mock_servers WHERE id = ?", (mock_id,)).fetchone()
    if not row:
        return None
    m = _row_to_mock(row)
    if with_routes:
        m["routes"] = list_mock_routes(m["id"])
    return m


def get_mock_by_slug(slug: str, with_routes: bool = True) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM mock_servers WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    m = _row_to_mock(row)
    if with_routes:
        m["routes"] = list_mock_routes(m["id"])
    return m


def create_mock(data: dict[str, Any]) -> dict[str, Any]:
    mid = uuid.uuid4().hex
    name = str(data.get("name") or "Mock server")
    slug = mock_slug(data.get("slug") or name)
    with _lock:
        _c().execute(
            "INSERT INTO mock_servers (id, name, slug, description, enabled, delay_ms, cors, log_calls, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, name, slug, str(data.get("description") or ""), 1 if data.get("enabled", True) else 0,
             max(0.0, float(data.get("delay_ms") or 0)), 1 if data.get("cors", True) else 0,
             1 if data.get("log_calls", True) else 0, time.time()),
        )
        _c().commit()
    for route in data.get("routes") or []:
        create_mock_route(mid, route)
    return get_mock(mid)  # type: ignore[return-value]


def update_mock(mock_id: str, **fields: Any) -> dict[str, Any] | None:
    if "slug" in fields:
        taken = {m["slug"] for m in list_mocks(with_routes=False) if m["id"] != mock_id}
        fields["slug"] = mock_slug(fields["slug"], taken)
    for key in ("enabled", "cors", "log_calls"):
        if key in fields:
            fields[key] = 1 if fields[key] else 0
    if "delay_ms" in fields:
        fields["delay_ms"] = max(0.0, float(fields["delay_ms"]))
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with _lock:
            _c().execute(f"UPDATE mock_servers SET {cols} WHERE id = ?", (*fields.values(), mock_id))
            _c().commit()
    return get_mock(mock_id)


def delete_mock(mock_id: str) -> bool:
    with _lock:
        cur = _c().execute("DELETE FROM mock_servers WHERE id = ?", (mock_id,))
        _c().commit()
    return cur.rowcount > 0


def list_mock_routes(mock_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM mock_routes WHERE server_id = ? ORDER BY position, rowid", (mock_id,),
        ).fetchall()
    return [_row_to_mock_route(r) for r in rows]


def get_mock_route(route_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _c().execute("SELECT * FROM mock_routes WHERE id = ?", (route_id,)).fetchone()
    return _row_to_mock_route(row) if row else None


def create_mock_route(mock_id: str, data: dict[str, Any]) -> dict[str, Any]:
    rid = uuid.uuid4().hex
    with _lock:
        pos = _c().execute("SELECT COALESCE(MAX(position), -1) + 1 FROM mock_routes WHERE server_id = ?",
                           (mock_id,)).fetchone()[0]
        _c().execute(
            "INSERT INTO mock_routes (id, server_id, name, method, path, status_code, headers, content_type, "
            "body, delay_ms, match_query, enabled, position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, mock_id, str(data.get("name") or ""), str(data.get("method") or "GET").upper(),
             str(data.get("path") or "/"), int(data.get("status_code") or 200),
             json.dumps(_kv_rows(data.get("headers"))), str(data.get("content_type") or "application/json"),
             str(data.get("body") or ""), max(0.0, float(data.get("delay_ms") or 0)),
             str(data.get("match_query") or ""), 1 if data.get("enabled", True) else 0, pos),
        )
        _c().commit()
    return get_mock_route(rid)  # type: ignore[return-value]


def update_mock_route(route_id: str, **fields: Any) -> dict[str, Any] | None:
    if "headers" in fields:
        fields["headers"] = json.dumps(_kv_rows(fields["headers"]))
    if "method" in fields:
        fields["method"] = str(fields["method"]).upper()
    if "status_code" in fields:
        fields["status_code"] = int(fields["status_code"])
    if "delay_ms" in fields:
        fields["delay_ms"] = max(0.0, float(fields["delay_ms"]))
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if fields:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with _lock:
            _c().execute(f"UPDATE mock_routes SET {cols} WHERE id = ?", (*fields.values(), route_id))
            _c().commit()
    return get_mock_route(route_id)


def delete_mock_route(route_id: str) -> bool:
    with _lock:
        cur = _c().execute("DELETE FROM mock_routes WHERE id = ?", (route_id,))
        _c().commit()
    return cur.rowcount > 0


def reorder_mock_routes(mock_id: str, route_ids: list[str]) -> None:
    with _lock:
        for pos, rid in enumerate(route_ids):
            _c().execute("UPDATE mock_routes SET position = ? WHERE id = ? AND server_id = ?", (pos, rid, mock_id))
        _c().commit()


def bump_mock_hits(mock_id: str, route_id: str | None) -> None:
    with _lock:
        _c().execute("UPDATE mock_servers SET hits = hits + 1 WHERE id = ?", (mock_id,))
        if route_id:
            _c().execute("UPDATE mock_routes SET hits = hits + 1 WHERE id = ?", (route_id,))
        _c().commit()


def add_mock_call(mock_id: str, call: dict[str, Any], keep: int = MAX_MOCK_CALLS) -> dict[str, Any]:
    cid = uuid.uuid4().hex
    with _lock:
        _c().execute(
            "INSERT INTO mock_calls (id, server_id, route_id, route_name, ts, method, path, query, status_code, "
            "matched, duration_ms, req_headers, req_body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, mock_id, call.get("route_id"), str(call.get("route_name") or ""), call["ts"],
             str(call.get("method") or ""), str(call.get("path") or ""), str(call.get("query") or ""),
             int(call.get("status_code") or 200), 1 if call.get("matched") else 0, call.get("duration_ms"),
             json.dumps(call.get("req_headers") or {}), str(call.get("req_body") or "")),
        )
        _c().execute(
            "DELETE FROM mock_calls WHERE server_id = ? AND id NOT IN "
            "(SELECT id FROM mock_calls WHERE server_id = ? ORDER BY ts DESC LIMIT ?)",
            (mock_id, mock_id, keep),
        )
        _c().commit()
    return {"id": cid, **call}


def list_mock_calls(mock_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        rows = _c().execute("SELECT * FROM mock_calls WHERE server_id = ? ORDER BY ts DESC LIMIT ?",
                            (mock_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["matched"] = bool(d["matched"])
        d["req_headers"] = json.loads(d["req_headers"])
        out.append(d)
    return out


def clear_mock_calls(mock_id: str) -> int:
    with _lock:
        cur = _c().execute("DELETE FROM mock_calls WHERE server_id = ?", (mock_id,))
        _c().commit()
    return cur.rowcount
