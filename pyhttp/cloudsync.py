"""Cloud sync: a project kept in step with a pyhttp account (app.pyhttp.com).

Sign in once (in the browser, with GitHub or Google); then a project can be *linked* to a
project in the cloud. From there on, what changes here goes up a few seconds after the last
edit, and what changed elsewhere (another computer, the web app) comes down within a second
or two: the app keeps one connection open on which the cloud says "this project changed"
(and still looks once a minute, in case that connection is down).

How it works. A linked project database gets a small change log, filled by triggers on its
tables (generated from the schema, so nothing here names pyhttp's columns). A sync

  1. reads which rows changed here since the last one,
  2. asks the cloud for rows changed there since the change number this computer last saw,
     and writes them in; a row changed on both sides, differently, is a *conflict*: it is
     left alone and the user is asked which version to keep,
  3. sends the rows changed here.

What syncs: requests, folders, environments, variables, mock servers and their routes,
cookies and the request history. What does not: settings (they belong to a computer),
monitors (each side runs its own), run results (their files stay where the run happened).

Values of variables marked secret stay on this computer unless *Include secret values* is
switched on: they go up empty, and an empty one coming down never overwrites a value here.

Nothing here runs until the user signs in and links a project.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from . import USER_AGENT, secretstore

DEFAULT_SERVER = "https://app.pyhttp.com"
SYNC_TABLES = ("requests", "folders", "environments", "variables", "mock_servers", "mock_routes", "cookies", "history")
SECRET_COLUMNS = {"environments": "variables", "folders": "variables"}   # JSON lists of {key, value, secret}
KEYCHAIN_NAME = "pyhttp-cloud-token"
DELAY = 1.0          # seconds of quiet after an edit before it is sent
POLL_SECONDS = 60.0
PUSH_ROWS = 200


class CloudError(Exception):
    """Something the user should read: not signed in, the server said no, no network."""


# --------------------------------------------------------------------- the project database
def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    keyed = sorted((r["pk"], r["name"]) for r in info if r["pk"])
    return [name for _, name in keyed] or ["rowid"]


def _tables(conn: sqlite3.Connection) -> list[str]:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return [t for t in SYNC_TABLES if t in have]


def row_id(table: str, pk: str) -> str:
    """The id of a row in the cloud: the same on every computer for the same table and key."""
    return f"{table}.{hashlib.sha256(pk.encode()).hexdigest()[:40]}"


def install(conn: sqlite3.Connection) -> None:
    """The change log, the conflict list and a set of triggers per synced table."""
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS _cloud_changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, pk TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS _cloud_meta (k TEXT PRIMARY KEY, v TEXT);"
        "CREATE TABLE IF NOT EXISTS _cloud_conflicts (id TEXT PRIMARY KEY, tbl TEXT NOT NULL, pk TEXT NOT NULL, cloud TEXT, n INTEGER, at REAL);")
    for table in _tables(conn):
        keys = _key_columns(conn, table)
        new = "json_array(" + ", ".join(f'NEW."{c}"' for c in keys) + ")"
        old = "json_array(" + ", ".join(f'OLD."{c}"' for c in keys) + ")"
        log = "INSERT INTO _cloud_changes (tbl, pk) VALUES ('{0}', {1});"
        conn.executescript(
            f'CREATE TRIGGER IF NOT EXISTS "_cloud_i_{table}" AFTER INSERT ON "{table}" BEGIN {log.format(table, new)} END;'
            f'CREATE TRIGGER IF NOT EXISTS "_cloud_u_{table}" AFTER UPDATE ON "{table}" BEGIN {log.format(table, old)} {log.format(table, new)} END;'
            f'CREATE TRIGGER IF NOT EXISTS "_cloud_d_{table}" AFTER DELETE ON "{table}" BEGIN {log.format(table, old)} END;')
    conn.commit()


def uninstall(conn: sqlite3.Connection) -> None:
    """Back to a database that knows nothing of the cloud (the project is no longer linked)."""
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE '\\_cloud\\_%' ESCAPE '\\'").fetchall():
        conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    for table in ("_cloud_changes", "_cloud_meta", "_cloud_conflicts"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def linked(path: Path) -> bool:
    conn = _connect(path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = '_cloud_meta'").fetchone():
            return False
        return conn.execute("SELECT 1 FROM _cloud_meta WHERE k = 'project' AND v <> ''").fetchone() is not None
    finally:
        conn.close()


def meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM _cloud_meta")}


def set_meta(conn: sqlite3.Connection, **values: Any) -> None:
    for key, value in values.items():
        conn.execute("INSERT OR REPLACE INTO _cloud_meta (k, v) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def enqueue_everything(conn: sqlite3.Connection) -> None:
    for table in _tables(conn):
        current = "json_array(" + ", ".join(f'"{c}"' for c in _key_columns(conn, table)) + ")"
        conn.execute(f"INSERT INTO _cloud_changes (tbl, pk) SELECT '{table}', {current} FROM \"{table}\"")
    conn.commit()


def _plain(value: Any) -> Any:
    return bytes(value) if isinstance(value, (bytes, memoryview)) else value


def _row(conn: sqlite3.Connection, table: str, pk: str) -> dict[str, Any] | None:
    where = " AND ".join(f'"{c}" IS ?' for c in _key_columns(conn, table))
    row = conn.execute(f'SELECT * FROM "{table}" WHERE {where}', json.loads(pk)).fetchone()
    return {k: _plain(row[k]) for k in row.keys()} if row is not None else None


def take(conn: sqlite3.Connection) -> tuple[int, dict[str, dict[str, Any]]]:
    """(up to which local change, {row id: the row as it is now}) for everything changed here."""
    changes = conn.execute("SELECT seq, tbl, pk FROM _cloud_changes ORDER BY seq").fetchall()
    tables = set(_tables(conn))
    items: dict[str, dict[str, Any]] = {}
    for change in changes:
        table, pk = change["tbl"], change["pk"]
        if table in tables and row_id(table, pk) not in items:
            items[row_id(table, pk)] = {"id": row_id(table, pk), "t": table, "k": pk, "d": _row(conn, table, pk)}
    return (changes[-1]["seq"] if changes else 0), items


def apply(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> None:
    """Write rows that came from the cloud, without them counting as changes made here."""
    conn.execute("BEGIN IMMEDIATE")   # nothing else writes until the echo is removed again
    mark = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM _cloud_changes").fetchone()[0]
    tables = set(_tables(conn))
    for item in items:
        table = item["t"]
        if table not in tables:
            continue
        keys = _key_columns(conn, table)
        if item["d"] is None:
            conn.execute(f'DELETE FROM "{table}" WHERE ' + " AND ".join(f'"{c}" IS ?' for c in keys), json.loads(item["k"]))
            continue
        columns = [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]
        names = [c for c in columns if c in item["d"]]   # a column this version does not have is left out
        if names:
            conn.execute(f'INSERT OR REPLACE INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in names)}) '
                         f'VALUES ({", ".join("?" for _ in names)})', [item["d"][c] for c in names])
    conn.execute("DELETE FROM _cloud_changes WHERE seq > ?", (mark,))
    conn.commit()


# ----------------------------------------------------------------------------- secrets
def without_secrets(table: str, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The row as it goes up when secret values stay here: each secret variable's value empty."""
    column = SECRET_COLUMNS.get(table)
    if row is None or not column or not row.get(column):
        return row
    try:
        variables = json.loads(row[column])
    except (TypeError, ValueError):
        return row
    if not isinstance(variables, list) or not any(isinstance(v, dict) and v.get("secret") and v.get("value") for v in variables):
        return row
    cleaned = [{**v, "value": ""} if isinstance(v, dict) and v.get("secret") else v for v in variables]
    return {**row, column: json.dumps(cleaned)}


def keeping_local_secrets(table: str, incoming: dict[str, Any] | None, here: dict[str, Any] | None) -> dict[str, Any] | None:
    """The row as it is written here: a secret that arrives empty keeps the value this computer has."""
    column = SECRET_COLUMNS.get(table)
    if incoming is None or here is None or not column:
        return incoming
    try:
        arriving, mine = json.loads(incoming.get(column) or "[]"), json.loads(here.get(column) or "[]")
    except (TypeError, ValueError):
        return incoming
    if not isinstance(arriving, list) or not isinstance(mine, list):
        return incoming
    local = {v.get("key"): v.get("value") for v in mine if isinstance(v, dict) and v.get("secret") and v.get("value")}
    merged = [{**v, "value": local[v.get("key")]} if isinstance(v, dict) and v.get("secret") and not v.get("value") and v.get("key") in local else v
              for v in arriving]
    return {**incoming, column: json.dumps(merged)}


# -------------------------------------------------------------------------------- wire
def to_wire(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: {"$b": base64.b64encode(v).decode()} if isinstance(v, bytes) else v for k, v in row.items()}


def from_wire(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: base64.b64decode(v["$b"]) if isinstance(v, dict) and set(v) == {"$b"} else v for k, v in row.items()}


# ----------------------------------------------------------------------------- the syncer
class Syncer:
    """One per app. ``project_path`` answers the database file of the open project."""

    def __init__(self, data_dir: Path, project_path: Callable[[], Path], transport: httpx.BaseTransport | None = None) -> None:
        self.file = Path(data_dir) / "cloud.json"
        # one keychain entry per data folder: a second copy of pyhttp on the same computer
        # (another --data-dir) signs in and out on its own
        self.keychain_name = f"{KEYCHAIN_NAME}-{hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:10]}"
        self.project_path = project_path
        self._transport = transport
        self._lock = threading.RLock()        # one sync at a time
        self._tlock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._poller: threading.Thread | None = None
        self._listener: threading.Thread | None = None
        self._stop = threading.Event()
        self.applied_rows: list[dict[str, str]] = []   # which rows the last change from the cloud touched
        self._login: dict[str, str] = {}      # state and verifier of a sign-in under way
        self._account: dict[str, Any] | None = None
        self.applied_at = 0.0                 # when rows from the cloud last changed this project (the UI reloads)
        self.syncing = False

    # ---------------------------------------------------------------- account
    def account(self) -> dict[str, Any]:
        if self._account is None:
            try:
                self._account = json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._account = {}
        return self._account

    def server(self) -> str:
        return (os.environ.get("PYHTTP_CLOUD_URL") or self.account().get("server") or DEFAULT_SERVER).rstrip("/")

    def _token(self) -> str:
        account = self.account()
        if account.get("token"):
            return str(account["token"])
        if account.get("keychain"):
            try:
                # (the plain name is where the first builds with sync kept it)
                return secretstore.get(self.keychain_name) or secretstore.get(KEYCHAIN_NAME) or ""
            except secretstore.SecretStoreError:
                return ""
        return ""

    def signed_in(self) -> bool:
        return bool(self.account().get("user")) and bool(self._token())

    def _save_account(self, account: dict[str, Any]) -> None:
        self._account = account
        if not account:
            self.file.unlink(missing_ok=True)
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(account, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.file)

    def _http(self, authed: bool = True) -> httpx.Client:
        headers = {"User-Agent": USER_AGENT}
        if authed:
            token = self._token()
            if not token:
                raise CloudError("Not signed in")
            headers["Authorization"] = f"Bearer {token}"
        return httpx.Client(base_url=self.server(), headers=headers, timeout=30, transport=self._transport)

    def _call(self, method: str, path: str, body: Any = None, authed: bool = True) -> Any:
        try:
            with self._http(authed) as client:
                response = client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise CloudError(f"The pyhttp cloud could not be reached ({type(exc).__name__})") from exc
        if response.status_code == 401:
            raise CloudError("This computer is no longer signed in. Sign in again.")
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 400:
            raise CloudError(str((data or {}).get("detail") or f"The pyhttp cloud answered {response.status_code}"))
        return data

    def login_url(self, port: int, device_name: str) -> str:
        """Where the browser goes to sign this computer in; it comes back to /api/cloud/callback."""
        verifier = secrets.token_urlsafe(48)
        self._login = {"state": secrets.token_urlsafe(24), "verifier": verifier}
        query = {"port": port, "state": self._login["state"], "challenge": hashlib.sha256(verifier.encode()).hexdigest(), "name": device_name[:80]}
        return f"{self.server()}/api/auth/device?{urlencode(query)}"

    def login_finish(self, code: str, state: str) -> dict[str, Any]:
        pending, self._login = self._login, {}
        if not pending or not secrets.compare_digest(pending.get("state", ""), state or ""):
            raise CloudError("That sign-in did not start here. Click Sign in again.")
        data = self._call("POST", "/api/auth/device/token", {"code": code, "verifier": pending["verifier"]}, authed=False)
        account: dict[str, Any] = {"server": self.server(), "user": data["user"]}
        try:   # the token belongs in the system keychain; a plain file only when there is none
            secretstore.put(self.keychain_name, data["token"])
            account["keychain"] = True
        except secretstore.SecretStoreError:
            account["token"] = data["token"]
        self._save_account(account)
        self.start()
        return data["user"]

    def logout(self) -> None:
        self.stop()
        if self.account().get("keychain"):
            try:
                secretstore.delete(self.keychain_name)
            except secretstore.SecretStoreError:
                pass
        self._save_account({})

    # ---------------------------------------------------------------- the project
    def _open(self) -> sqlite3.Connection:
        return _connect(self.project_path())

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"signed_in": self.signed_in(), "user": self.account().get("user"), "server": self.server(),
                               "linked": False, "syncing": self.syncing, "applied_at": self.applied_at, "applied_rows": self.applied_rows}
        try:
            path = self.project_path()
            if not linked(path):
                return out
            conn = _connect(path)
            try:
                m = meta(conn)
                conflicts = [{"id": r["id"], "table": r["tbl"], "name": self._label(conn, r["tbl"], r["pk"], r["cloud"]),
                              "deleted_in_cloud": r["cloud"] is None, "deleted_here": _row(conn, r["tbl"], r["pk"]) is None}
                             for r in conn.execute("SELECT * FROM _cloud_conflicts ORDER BY at")]
                pending = conn.execute("SELECT COUNT(DISTINCT tbl || pk) FROM _cloud_changes").fetchone()[0]
            finally:
                conn.close()
            out.update(linked=True, project=m.get("project"), project_name=m.get("project_name") or "", last_sync=float(m.get("last_sync") or 0),
                       error=m.get("error") or "", include_secrets=m.get("secrets") == "1", pending=pending, conflicts=conflicts)
        except (sqlite3.Error, OSError) as exc:
            out["error"] = str(exc)
        return out

    @staticmethod
    def _label(conn: sqlite3.Connection, table: str, pk: str, cloud: str | None) -> str:
        row = _row(conn, table, pk) or (json.loads(cloud) if cloud else {})
        return str(row.get("name") or row.get("key") or row.get("url") or json.loads(pk)[0])

    def remote_projects(self) -> list[dict[str, Any]]:
        return self._call("GET", "/api/sync/projects")["projects"]

    def link(self, mode: str, cloud_id: str = "", name: str = "") -> dict[str, Any]:
        """``upload``: this project becomes a new project in the cloud. ``load``: this project's
        synced content is replaced by the cloud project ``cloud_id``."""
        if not self.signed_in():
            raise CloudError("Sign in first")
        with self._lock:
            if mode == "upload":
                made = self._call("POST", "/api/sync/projects", {"name": name or "From my computer"})
                cloud_id, project_name = made["id"], made["name"]
            elif mode == "load":
                known = {p["id"]: p["name"] for p in self.remote_projects()}
                if cloud_id not in known:
                    raise CloudError("That project is not in your account")
                project_name = known[cloud_id]
            else:
                raise CloudError("mode must be upload or load")
            conn = self._open()
            try:
                uninstall(conn)
                install(conn)
                if mode == "load":   # what is here makes way; the triggers log it, and the log is emptied
                    for table in _tables(conn):
                        conn.execute(f'DELETE FROM "{table}"')
                    conn.execute("DELETE FROM _cloud_changes")
                    conn.commit()
                else:
                    enqueue_everything(conn)
                set_meta(conn, project=cloud_id, project_name=project_name, cursor=0, secrets="0", error="")
            finally:
                conn.close()
            self.start()
            return self.sync()

    def unlink(self) -> None:
        with self._lock:
            conn = self._open()
            try:
                uninstall(conn)
            finally:
                conn.close()

    def set_include_secrets(self, on: bool) -> None:
        with self._lock:
            conn = self._open()
            try:
                set_meta(conn, secrets="1" if on else "0")
                if on:   # values held back so far go up now
                    for table in SECRET_COLUMNS:
                        if table in _tables(conn):
                            current = "json_array(" + ", ".join(f'"{c}"' for c in _key_columns(conn, table)) + ")"
                            conn.execute(f"INSERT INTO _cloud_changes (tbl, pk) SELECT '{table}', {current} FROM \"{table}\"")
                    conn.commit()
            finally:
                conn.close()
        self.touch()

    # -------------------------------------------------------------------- sync
    def sync(self) -> dict[str, Any]:
        """One round for the open project. Errors are kept for the status and raised."""
        with self._lock:
            path = self.project_path()
            if not self.signed_in() or not linked(path):
                return self.status()
            self.syncing = True
            conn = _connect(path)
            try:
                install(conn)   # a table added by an update gets its triggers
                changed = self._round(conn)
                set_meta(conn, last_sync=time.time(), error="")
                if changed:
                    self.applied_at = time.time()
            except CloudError as exc:
                set_meta(conn, error=str(exc))
                raise
            finally:
                conn.close()
                self.syncing = False
            return self.status()

    def _round(self, conn: sqlite3.Connection) -> bool:
        m = meta(conn)
        pid, cursor, secrets_too = m["project"], int(m.get("cursor") or 0), m.get("secrets") == "1"
        outgoing = (lambda t, d: d) if secrets_too else without_secrets
        upto, pending = take(conn)
        conflicted = {r["id"] for r in conn.execute("SELECT id FROM _cloud_conflicts")}
        changed = False

        while True:   # what changed elsewhere
            got = self._call("POST", f"/api/sync/{pid}/pull", {"cursor": cursor})
            incoming = []
            for row in got["rows"]:
                theirs = from_wire(row["d"])
                mine = pending.get(row["id"])
                if mine is not None and outgoing(mine["t"], mine["d"]) == theirs:
                    del pending[row["id"]]   # both sides made the same change
                elif mine is not None or row["id"] in conflicted:
                    self._conflict(conn, row["id"], row["t"], row["k"], theirs, row["n"])
                    conflicted.add(row["id"])
                else:
                    here = _row(conn, row["t"], row["k"])
                    if outgoing(row["t"], here) != theirs:   # our own pushes come back; they change nothing
                        incoming.append({"t": row["t"], "k": row["k"], "d": keeping_local_secrets(row["t"], theirs, here)})
            if incoming:
                apply(conn, incoming)
                if not changed:
                    self.applied_rows = []
                self.applied_rows = (self.applied_rows + [{"t": i["t"], "k": i["k"]} for i in incoming])[-500:]
                changed = True
            cursor = int(got["cursor"])
            set_meta(conn, cursor=cursor)
            if not got.get("more"):
                break

        mine = [i for i in pending.values() if i["id"] not in conflicted]   # what changed here
        for start in range(0, len(mine), PUSH_ROWS):
            chunk = mine[start:start + PUSH_ROWS]
            answer = self._call("POST", f"/api/sync/{pid}/push", {
                "cursor": cursor, "rows": [{"t": i["t"], "k": i["k"], "d": to_wire(outgoing(i["t"], i["d"]))} for i in chunk]})
            for c in answer.get("conflicts") or []:   # changed in the cloud between our pull and this push
                self._conflict(conn, c["id"], c["t"], c["k"], from_wire(c["d"]), c["n"])
        if upto:
            conn.execute("DELETE FROM _cloud_changes WHERE seq <= ?", (upto,))
            conn.commit()
        return changed

    @staticmethod
    def _conflict(conn: sqlite3.Connection, rid: str, table: str, pk: str, cloud: dict[str, Any] | None, n: int) -> None:
        stored = json.dumps(to_wire(cloud)) if cloud is not None else None
        conn.execute("INSERT OR REPLACE INTO _cloud_conflicts (id, tbl, pk, cloud, n, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (rid, table, pk, stored, n, time.time()))
        conn.commit()

    def resolve(self, rid: str, keep: str) -> dict[str, Any]:
        """Settle a conflict: ``keep`` is "cloud" (their version is written here) or "mine"
        (this computer's version replaces the cloud's)."""
        if keep not in ("cloud", "mine"):
            raise CloudError("keep must be cloud or mine")
        with self._lock:
            conn = self._open()
            try:
                c = conn.execute("SELECT * FROM _cloud_conflicts WHERE id = ?", (rid,)).fetchone()
                if c is None:
                    raise CloudError("That conflict is already settled")
                m = meta(conn)
                if keep == "cloud":
                    theirs = from_wire(json.loads(c["cloud"])) if c["cloud"] else None
                    apply(conn, [{"t": c["tbl"], "k": c["pk"], "d": keeping_local_secrets(c["tbl"], theirs, _row(conn, c["tbl"], c["pk"]))}])
                    self.applied_rows = [{"t": c["tbl"], "k": c["pk"]}]
                    self.applied_at = time.time()
                else:
                    outgoing = (lambda t, d: d) if m.get("secrets") == "1" else without_secrets
                    row = outgoing(c["tbl"], _row(conn, c["tbl"], c["pk"]))
                    self._call("POST", f"/api/sync/{m['project']}/push", {
                        "cursor": int(m.get("cursor") or 0), "force": [rid], "rows": [{"t": c["tbl"], "k": c["pk"], "d": to_wire(row)}]})
                conn.execute("DELETE FROM _cloud_conflicts WHERE id = ?", (rid,))
                conn.commit()
            finally:
                conn.close()
        return self.status()

    # ------------------------------------------------------------- background
    def touch(self) -> None:
        """Something may have changed: sync once things have been quiet for DELAY seconds."""
        if not self._account and not self.file.exists():
            return   # never signed in: nothing to do, and nothing to read
        with self._tlock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DELAY, self._quiet_sync)
            self._timer.daemon = True
            self._timer.start()

    def _quiet_sync(self) -> None:
        try:
            self.sync()
        except Exception:  # noqa: BLE001 - kept in the status; the next round tries again
            pass

    def start(self) -> None:
        """Listen to the cloud, and look at it on a timer as well. Does nothing for an app
        that is not signed in."""
        if not self.signed_in() or (self._poller and self._poller.is_alive()):
            return
        self._stop.clear()
        self._poller = threading.Thread(target=self._poll, daemon=True, name="pyhttp-cloud-sync")
        self._poller.start()
        self._listener = threading.Thread(target=self._listen, daemon=True, name="pyhttp-cloud-listen")
        self._listener.start()

    def _linked_project(self) -> tuple[str, int]:
        """(the cloud project the open project is linked to or "", how far this computer has pulled)."""
        try:
            path = self.project_path()
            if not linked(path):
                return "", 0
            conn = _connect(path)
            try:
                m = meta(conn)
            finally:
                conn.close()
            return m.get("project") or "", int(m.get("cursor") or 0)
        except (sqlite3.Error, OSError, AssertionError):
            return "", 0

    def _listen(self) -> None:
        """One open connection on which the cloud sends a line when the linked project changes
        (the web app, another computer). Each line is answered with a sync, at once."""
        wait = 2.0
        while not self._stop.is_set():
            pid, cursor = self._linked_project()
            if not pid or not self.signed_in():
                if self._stop.wait(5):
                    return
                continue
            try:
                with self._http() as client, client.stream("GET", f"/api/sync/{pid}/events", params={"cursor": cursor},
                                                           timeout=httpx.Timeout(30, read=75)) as stream:
                    if stream.status_code != 200:
                        raise httpx.HTTPError(f"events answered {stream.status_code}")
                    wait = 2.0
                    for line in stream.iter_lines():   # "data: {...}" on a change, ": ping" every 20 seconds
                        if self._stop.is_set() or self._linked_project()[0] != pid:
                            break   # stopped, or another project was opened: listen for that one
                        if line.startswith("data:"):
                            self._quiet_sync()
            except (httpx.HTTPError, CloudError, OSError):
                wait = min(wait * 2, 60.0)   # no network, or the server restarting: the timer still syncs
            if self._stop.wait(wait):
                return

    def _poll(self) -> None:
        self._quiet_sync()
        while not self._stop.wait(POLL_SECONDS):
            self._quiet_sync()

    def stop(self) -> None:
        self._stop.set()
        with self._tlock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
