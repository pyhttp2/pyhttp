"""Projects: separate workspaces inside one data folder.

Each project owns a database (requests, folders, environments, variables, history, runs,
cookies, monitors, mocks, settings) and an output folder for run files. The data that
existed before projects were introduced is the *Default* project and stays exactly where
it was (``<data_dir>/pyhttp.db`` and ``<data_dir>/outputs``); every other project lives in
``<data_dir>/projects/<id>/``. The registry is a small JSON file next to them.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_ID = "default"
DEFAULT_NAME = "Default"
MAX_NAME = 80


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:32] or "project"


class Registry:
    """The list of projects and which one is active. Thread-safe; writes go straight to disk."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file = data_dir / "projects.json"
        self._lock = threading.RLock()
        self._data = self._load()

    # ---------------------------------------------------------------- storage
    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        projects = [dict(p) for p in (raw.get("projects") or [])
                    if isinstance(p, dict) and p.get("id") and str(p.get("name") or "").strip()]
        if not any(p["id"] == DEFAULT_ID for p in projects):
            projects.insert(0, {"id": DEFAULT_ID, "name": DEFAULT_NAME, "created_at": 0.0})
        active = raw.get("active")
        if not any(p["id"] == active for p in projects):
            active = DEFAULT_ID
        return {"active": active, "projects": projects}

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    # ---------------------------------------------------------------- reading
    def active(self) -> str:
        with self._lock:
            return self._data["active"]

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            return next((dict(p) for p in self._data["projects"] if p["id"] == pid), None)

    def find(self, name_or_id: str) -> dict[str, Any] | None:
        """A project by id, or by name (case-insensitive)."""
        with self._lock:
            key = (name_or_id or "").strip().lower()
            return next((dict(p) for p in self._data["projects"]
                         if p["id"] == name_or_id or p["name"].lower() == key), None)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            active = self._data["active"]
            out = []
            for p in self._data["projects"]:
                db_path, out_dir = self.paths(p["id"])
                try:
                    size = db_path.stat().st_size
                except OSError:
                    size = 0
                out.append({**p, "active": p["id"] == active, "dir": str(db_path.parent), "db_size": size})
            return out

    def paths(self, pid: str) -> tuple[Path, Path]:
        """(database file, output folder) of a project. The Default project keeps the legacy layout."""
        if pid == DEFAULT_ID:
            return self.data_dir / "pyhttp.db", self.data_dir / "outputs"
        root = self.data_dir / "projects" / pid
        return root / "pyhttp.db", root / "outputs"

    # ---------------------------------------------------------------- changes
    def _check_name(self, name: str, except_id: str | None = None) -> str:
        name = " ".join((name or "").split())
        if not name:
            raise ValueError("Give the project a name")
        if len(name) > MAX_NAME:
            raise ValueError(f"Project names are at most {MAX_NAME} characters")
        if any(p["name"].lower() == name.lower() and p["id"] != except_id for p in self._data["projects"]):
            raise ValueError(f"There is already a project called “{name}”")
        return name

    def create(self, name: str) -> dict[str, Any]:
        with self._lock:
            name = self._check_name(name)
            pid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
            while any(p["id"] == pid for p in self._data["projects"]):
                pid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
            project = {"id": pid, "name": name, "created_at": time.time()}
            db_path, out_dir = self.paths(pid)
            out_dir.mkdir(parents=True, exist_ok=True)
            self._data["projects"].append(project)
            self._save()
            return dict(project)

    def rename(self, pid: str, name: str) -> dict[str, Any]:
        with self._lock:
            p = next((p for p in self._data["projects"] if p["id"] == pid), None)
            if p is None:
                raise KeyError(pid)
            p["name"] = self._check_name(name, except_id=pid)
            self._save()
            return dict(p)

    def set_active(self, pid: str) -> None:
        with self._lock:
            if self.get(pid) is None:
                raise KeyError(pid)
            self._data["active"] = pid
            self._save()

    def delete(self, pid: str) -> None:
        """Remove a project and its files. The active project cannot be deleted (switch first)."""
        with self._lock:
            if self.get(pid) is None:
                raise KeyError(pid)
            if pid == self._data["active"]:
                raise ValueError("Switch to another project before deleting this one")
            db_path, out_dir = self.paths(pid)
            if pid == DEFAULT_ID:
                for suffix in ("", "-wal", "-shm"):
                    Path(str(db_path) + suffix).unlink(missing_ok=True)
                shutil.rmtree(out_dir, ignore_errors=True)
            else:
                shutil.rmtree(db_path.parent, ignore_errors=True)
            self._data["projects"] = [p for p in self._data["projects"] if p["id"] != pid]
            if not any(p["id"] == DEFAULT_ID for p in self._data["projects"]):
                # the Default project is always listed; deleting it just empties it
                self._data["projects"].insert(0, {"id": DEFAULT_ID, "name": DEFAULT_NAME, "created_at": 0.0})
            self._save()
