"""Git sync: the project's definitions as files in a Git repository, kept in step both ways.

A sync writes the definitions (requests, folders,
environments, variables, mocks, monitors — no history, run outputs or cookies) as one JSON file
each into the configured folder, commits what changed and pushes it:

    pyhttp.json                  marker: this folder is written by pyhttp
    variables.json               global variables
    monitors.json
    collection/<Folder>/_folder.json, collection/<Folder>/<Request>.json
    environments/<Name>.json
    mocks/<Name>.json

The folder may be a repository, a folder inside one (the definitions next to the code they
test), or neither — then a repository is created in it. Only the paths above are ever staged,
committed or deleted, so other work in the same repository is left alone.

It also reads back. Commits that reach the folder from elsewhere — pulled from the remote, or
made by hand — are folded into the project: the files they changed replace what the project has,
everything else stays as it is in the app. ``git_applied`` remembers the commit the project is
level with, so "what changed elsewhere" is always a plain ``git diff`` from there to HEAD. Two
situations need a person and are reported as a conflict instead of guessed at: turning sync on
for a folder that already holds a different pyhttp project, and a merge where both sides changed
the same thing.

Settings live with the project (``git_*`` keys), so every project syncs to its own folder.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import db

MANIFEST = "pyhttp.json"
MANAGED_DIRS = ("collection", "environments", "mocks")
MANAGED = (MANIFEST, "variables.json", "monitors.json", *MANAGED_DIRS)
FOLDER_FILE = "_folder"
_BOOLS = {"auto_commit": True, "auto_push": True, "auto_pull": True, "secrets": False}   # name -> default
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class GitError(Exception):
    pass


class Conflict(GitError):
    """Something only the user can decide: ``kind`` is "adopt" (the folder already holds another
    pyhttp project) or "merge" (the remote and this project changed the same thing)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# ------------------------------------------------------------------- files
def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _filename(name: str, ident: str, used: set[str]) -> str:
    """A readable file name for ``name`` that is valid on every OS and unique in its folder
    (case-insensitively); a clash gets a piece of the id appended."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(name or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")[:60].strip(" .") or "untitled"
    if s.lower() in _RESERVED:
        s += "_"
    if s.lower() in used:
        s = f"{s} ({ident[:6]})"
    while s.lower() in used:
        s += "_"
    used.add(s.lower())
    return s


def _mask(rows: Any, secrets: bool) -> Any:
    if secrets or not isinstance(rows, list):
        return rows
    return [{**r, "value": ""} if isinstance(r, dict) and r.get("secret") else r for r in rows]


def build_tree(payload: dict[str, Any], secrets: bool = False) -> dict[str, str]:
    """``{relative path: file text}`` for a whole-project export (see ``/api/export``).
    Unless ``secrets`` is set, the values of variables marked secret are written empty."""
    files: dict[str, str] = {MANIFEST: _dump({"format": 1}),
                             "variables.json": _dump(payload.get("variables") or {}),
                             "monitors.json": _dump(payload.get("monitors") or [])}
    used: dict[str, set[str]] = {}   # folder path -> names taken in it

    def name_in(path: str, name: str, ident: str) -> str:
        return _filename(name, ident, used.setdefault(path, {FOLDER_FILE}))

    # Names are handed out in id order, so two things with the same name keep their file names
    # when they are reordered in the UI.
    folders = {f["id"]: f for f in payload.get("folders") or []}
    paths: dict[str, str] = {}

    def folder_path(fid: str | None, seen: tuple[str, ...] = ()) -> str:
        if not fid or fid not in folders or fid in seen:
            return "collection"
        if fid not in paths:
            parent = folder_path(folders[fid].get("parent_id"), (*seen, fid))
            paths[fid] = f"{parent}/{name_in(parent, folders[fid].get('name'), fid)}"
        return paths[fid]

    for fid in sorted(folders):
        f = folders[fid]
        data = {k: v for k, v in f.items() if k not in ("created_at", "updated_at")}
        data["variables"] = _mask(data.get("variables"), secrets)
        files[f"{folder_path(fid)}/{FOLDER_FILE}.json"] = _dump(data)
    for r in sorted(payload.get("requests") or [], key=lambda r: str(r.get("id") or "")):
        path = folder_path(r.get("folder_id"))
        files[f"{path}/{name_in(path, r.get('name'), str(r.get('id') or ''))}.json"] = _dump(r)
    for e in sorted(payload.get("environments") or [], key=lambda e: str(e.get("name") or "")):
        data = {**e, "variables": _mask(e.get("variables"), secrets)}
        files[f"environments/{name_in('environments', e.get('name'), '')}.json"] = _dump(data)
    for m in sorted(payload.get("mocks") or [], key=lambda m: str(m.get("slug") or m.get("name") or "")):
        files[f"mocks/{name_in('mocks', m.get('slug') or m.get('name'), '')}.json"] = _dump(m)
    return files


def write_tree(root: Path, files: dict[str, str]) -> int:
    """Make ``root`` hold exactly ``files`` under the managed paths; returns how many files changed.
    Files that are already right are not touched, and JSON files of things that no longer exist
    are removed. Anything else in ``root`` is left alone."""
    if not (root / MANIFEST).exists():
        taken = [d for d in MANAGED_DIRS if (root / d).exists()]
        if taken:
            raise GitError(f"{root} already has a “{taken[0]}” folder that pyhttp did not write. "
                           "Pick an empty folder, or a new folder inside your repository.")
    changed = 0
    for rel, text in files.items():
        target = root / rel
        data = text.encode("utf-8")
        try:
            if target.read_bytes() == data:
                continue
        except OSError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed += 1
    keep = {(root / rel).resolve() for rel in files}
    for d in MANAGED_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*"), reverse=True):   # children before their folders
            if path.is_file() and path.suffix == ".json" and path.resolve() not in keep:
                path.unlink()
                changed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    return changed


def read_tree(root: Path) -> dict[str, str]:
    """The managed files in ``root`` as ``{relative path: text}`` — what ``build_tree`` would give
    for the project they were written from."""
    files: dict[str, str] = {}
    for name in MANAGED:
        path = root / name
        found = [path] if path.is_file() else sorted(path.rglob("*.json")) if path.is_dir() else []
        for f in found:
            files[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8")
    return files


def parse_tree(files: dict[str, str]) -> dict[str, Any]:
    """The reverse of ``build_tree``: files back to a whole-project payload. Where a thing sits is
    taken from its content (``folder_id``, ``parent_id``), not from the path of its file."""
    payload: dict[str, Any] = {"folders": [], "requests": [], "environments": [], "mocks": [], "variables": {}, "monitors": []}
    for rel in sorted(files):
        try:
            data = json.loads(files[rel])
        except ValueError as exc:
            raise GitError(f"{rel} is not valid JSON ({exc}). Fix or restore the file, then sync again.") from exc
        top, name = rel.split("/", 1)[0], Path(rel).stem
        if rel == "variables.json" and isinstance(data, dict):
            payload["variables"] = data
        elif rel == "monitors.json" and isinstance(data, list):
            payload["monitors"] = data
        elif not isinstance(data, dict) or rel == MANIFEST:
            continue
        elif top == "collection":
            payload["folders" if name == FOLDER_FILE else "requests"].append(data)
        elif top in ("environments", "mocks"):
            payload[top].append(data)
    return payload


def _is_empty(payload: dict[str, Any]) -> bool:
    return not any(payload.get(k) for k in ("folders", "requests", "environments", "mocks", "monitors", "variables"))


# --------------------------------------------------------------------- git
def _run(root: Path, *args: str, check: bool = True, interactive: bool = False, timeout: float = 60) -> tuple[int, str]:
    """Run git in ``root``. Unless ``interactive``, nothing may ask for a password: a background
    push that needs credentials fails with a message instead of hanging or popping up a window."""
    exe = shutil.which("git")
    if not exe:
        raise GitError("Git is not installed (or not on PATH). Install it from https://git-scm.com and try again.")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    if not interactive:
        env["GCM_INTERACTIVE"] = "never"
    verb = next((a for a in args if not a.startswith("-") and "=" not in a), "command")
    try:
        proc = subprocess.run([exe, "-C", str(root), *args], env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {verb} did not finish within {int(timeout)} seconds") from exc
    except OSError as exc:
        raise GitError(f"Could not run git: {exc}") from exc
    out = (proc.stdout or "").rstrip("\r\n")   # not strip(): a status line may start with a space
    if check and proc.returncode != 0:
        err = (proc.stderr or "").strip() or out.strip() or f"exit code {proc.returncode}"
        raise GitError(f"git {verb} failed: {err[-600:]}")
    return proc.returncode, out


def _changed_paths(status: str) -> list[str]:
    """Paths (relative to the repository) from ``git status --porcelain -z`` output."""
    out = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        out.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            next(entries, None)   # a rename or copy is followed by the path it came from
    return out


def _present(prefix: str, changed: list[str]) -> list[str]:
    """The managed top-level names that have changes. ``git add`` refuses a path that matches
    nothing, so only the ones ``git status`` reported are passed on. Status paths are relative to
    the repository; ``prefix`` is where the synced folder sits in it ("" or "some/folder/")."""
    found: list[str] = []
    for p in changed:
        first = (p[len(prefix):] if p.startswith(prefix) else p).split("/", 1)[0]
        if first in MANAGED and first not in found:
            found.append(first)
    if not found:   # never fall through to "git add -A" without a path: that would stage everything
        raise GitError("Could not work out which files changed")
    return found


def _message(paths: list[str]) -> str:
    names: list[str] = []
    for p in paths:
        stem = Path(p).stem
        name = Path(p).parent.name if stem == FOLDER_FILE else stem
        if name not in names and Path(p).name != MANIFEST:
            names.append(name)
    if not names:
        return "pyhttp: update project"
    shown = ", ".join(names[:3])
    more = len(names) - 3
    return f"pyhttp: update {shown}" + (f" (+{more} more)" if more > 0 else "")


# ------------------------------------------------------------------ syncer
class Syncer:
    """Keeps the configured folder in step with the open project. ``touch()`` after a change
    schedules a sync a few seconds later (restarted by every further change), so a burst of
    autosaves becomes one commit."""

    DELAY = 3.0   # seconds of quiet before a change is committed

    def __init__(self, payload: Callable[[], dict[str, Any]]) -> None:
        self._payload = payload
        self._lock = threading.RLock()        # one sync at a time
        self._tlock = threading.Lock()        # the timer only: touch() must never wait for a push
        self._timer: threading.Timer | None = None
        self._state: dict[str, Any] = {}
        self._pulled = False                  # the running sync changed the project

    # ------------------------------------------------------------- settings
    def config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {"dir": (db.get_setting("git_dir") or "").strip(),
                               "remote": (db.get_setting("git_remote") or "").strip()}
        for key, default in _BOOLS.items():
            raw = db.get_setting(f"git_{key}")
            cfg[key] = default if raw is None else raw != "0"
        return cfg

    def set_config(self, patch: dict[str, Any]) -> None:
        if patch.get("dir") is not None:
            raw = str(patch["dir"]).strip()
            if raw:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    raise ValueError("Give the full path of the folder, for example " + str(Path.home() / "pyhttp-git"))
                if path.exists() and not path.is_dir():
                    raise ValueError(f"{path} is a file, not a folder")
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise ValueError(f"Could not create {path}: {exc}") from exc
                raw = str(path)
            if raw != (db.get_setting("git_dir") or ""):
                self._state = {}   # another folder: the old status says nothing about it
                db.set_setting("git_applied", None)
            db.set_setting("git_dir", raw)
        if patch.get("remote") is not None:
            db.set_setting("git_remote", str(patch["remote"]).strip())
        for key in _BOOLS:
            if patch.get(key) is not None:
                db.set_setting(f"git_{key}", "1" if patch[key] else "0")

    # ------------------------------------------------------------- schedule
    def touch(self) -> None:
        """Something may have changed: sync once things have been quiet for DELAY seconds."""
        try:
            if not self.config()["dir"]:
                return
        except Exception:  # noqa: BLE001 - the database is being switched or closed
            return
        with self._tlock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.DELAY, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _cancel(self) -> bool:
        """Drop the scheduled sync; true when one was waiting."""
        with self._tlock:
            waiting = self._timer is not None
            if self._timer:
                self._timer.cancel()
            self._timer = None
            return waiting

    def _fire(self) -> None:
        try:
            self.sync()
        except Exception:  # noqa: BLE001 - sync() records its own errors; never kill the timer thread loudly
            pass

    def flush(self) -> None:
        """Sync now if a change is still waiting (before the project is switched or the app closes)."""
        if self._cancel():
            self.sync()

    def reset(self) -> None:
        """Forget everything about the previous project (after a project switch)."""
        self._cancel()
        with self._lock:
            self._state = {}

    # ----------------------------------------------------------------- sync
    def sync(self, force: bool = False, interactive: bool = False, prefer: str = "") -> dict[str, Any]:
        """Bring the folder, the repository, the remote and the project level with each other.
        ``force`` commits, pulls and pushes even when the automatic switches are off (the “Sync
        now” button); ``interactive`` lets Git ask for credentials; ``prefer`` ("mine" or
        "theirs") settles a reported conflict."""
        with self._lock:
            self._cancel()   # this sync covers whatever was waiting; later changes schedule their own
            cfg = self.config()
            if not cfg["dir"]:
                return self.status()
            self._state["syncing"] = True
            self._pulled = False
            try:
                self._state.update(self._sync(Path(cfg["dir"]), cfg, force, interactive, prefer), error="", conflict="")
            except Conflict as exc:
                self._state.update(error=str(exc), conflict=exc.kind)
            except GitError as exc:
                self._state.update(error=str(exc), conflict="")
            except Exception as exc:  # noqa: BLE001 - shown in the UI instead of lost in a thread
                self._state.update(error=f"{type(exc).__name__}: {exc}", conflict="")
            finally:
                self._state["syncing"] = False
                self._state["synced_at"] = time.time()
                if self._pulled:   # the project itself changed: the UI reloads when this moves
                    self._state["pulled_at"] = time.time()
            return self.status()

    def _sync(self, root: Path, cfg: dict[str, Any], force: bool, interactive: bool, prefer: str) -> dict[str, Any]:
        root.mkdir(parents=True, exist_ok=True)
        fresh = _run(root, "rev-parse", "--is-inside-work-tree", check=False)[0] != 0
        if fresh:
            _run(root, "init")
        if cfg["remote"]:
            code, url = _run(root, "remote", "get-url", "origin", check=False)
            if code != 0:
                _run(root, "remote", "add", "origin", cfg["remote"])
            elif url != cfg["remote"]:
                _run(root, "remote", "set-url", "origin", cfg["remote"])
        remotes = _run(root, "remote")[1].split()
        remote = "origin" if "origin" in remotes else (remotes[0] if remotes else "")
        if fresh and remote:
            self._start_from_remote(root, remote, interactive)
        ident = [] if _run(root, "config", "user.email", check=False)[1] else \
            ["-c", "user.name=pyhttp", "-c", "user.email=pyhttp@localhost"]
        self._adopt(root, cfg, prefer)
        self._catch_up(root, cfg)
        dirty = self._commit(root, cfg, force, ident)
        if remote and (cfg["auto_pull"] or force) and _head(root):
            _run(root, "fetch", remote, interactive=interactive, timeout=90)
            up = _upstream(root, remote)
            if up and _run(root, "rev-list", "--count", f"HEAD..{up}")[1] != "0":
                self._merge(root, up, ident, prefer)
                if self._catch_up(root, cfg):
                    dirty = self._commit(root, cfg, force, ident)   # only if reading it back changed a file
        out: dict[str, Any] = {"repo": _run(root, "rev-parse", "--show-toplevel")[1],
                               "branch": _run(root, "branch", "--show-current", check=False)[1],
                               "dirty": dirty, "remote_name": remote, "commit": None, "unpushed": False}
        if _head(root):
            line = _run(root, "log", "-1", "--format=%h%x1f%s%x1f%ct")[1].split("\x1f")
            if len(line) == 3:
                out["commit"] = {"hash": line[0], "subject": line[1], "at": float(line[2] or 0)}
            if remote:
                up = _upstream(root, remote)
                out["unpushed"] = not up or _run(root, "rev-list", "--count", f"{up}..HEAD")[1] != "0"
                if out["unpushed"] and (cfg["auto_push"] or force):
                    _run(root, "push", "-u", remote, "HEAD", interactive=interactive, timeout=90)
                    out["unpushed"] = False
                    out["pushed_at"] = time.time()
        return out

    def _start_from_remote(self, root: Path, remote: str, interactive: bool) -> None:
        """A repository that was just created here, with a remote that may already have commits:
        start from the remote's default branch, like a clone would."""
        _run(root, "fetch", remote, interactive=interactive, timeout=90)
        found = re.search(r"ref: refs/heads/(\S+)\s+HEAD", _run(root, "ls-remote", "--symref", remote, "HEAD",
                                                                 check=False, interactive=interactive, timeout=90)[1])
        if found and _run(root, "rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{found.group(1)}", check=False)[0] == 0:
            _run(root, "checkout", "-B", found.group(1), f"{remote}/{found.group(1)}")

    def _adopt(self, root: Path, cfg: dict[str, Any], prefer: str) -> None:
        """First sync with this folder. If it already holds a pyhttp project, an empty project
        loads it; a project with content of its own must be told which side wins."""
        if db.get_setting("git_applied"):
            return
        if (root / MANIFEST).exists():
            there = read_tree(root)
            mine = self._payload()
            if there != build_tree(mine, cfg["secrets"]):
                if not prefer and not _is_empty(mine):
                    n = sum(1 for rel in there if rel.startswith("collection/") and Path(rel).stem != FOLDER_FILE)
                    raise Conflict("adopt", f"This folder already holds a pyhttp project ({n} request{'' if n == 1 else 's'}) "
                                            "that differs from this one. Choose which one to keep.")
                if prefer != "mine":
                    db.apply_definitions(parse_tree(there), keep_secrets=not cfg["secrets"])
                    self._pulled = True
        db.set_setting("git_applied", _head(root) or "none")

    def _catch_up(self, root: Path, cfg: dict[str, Any]) -> bool:
        """Fold commits that reached the folder from elsewhere into the project: the files changed
        between the commit the project is level with and HEAD replace what the project has (edits
        made here since and not yet committed stay). True when the project changed."""
        applied, head = db.get_setting("git_applied"), _head(root)
        if not head or applied == head:
            return False
        code, diff = (1, "") if not applied or applied == "none" else _run(
            root, "diff", "--name-status", "--no-renames", "--relative", "-z", applied, head, "--", *MANAGED, check=False)
        parts = diff.split("\0") if code == 0 else []
        changes = list(zip(parts[0::2], parts[1::2]))
        if changes:
            files = build_tree(self._payload(), secrets=True)
            for status, rel in changes:
                if status == "D":
                    files.pop(rel, None)
                else:
                    files[rel] = _run(root, "show", f"{head}:./{rel}")[1] + "\n"
            db.apply_definitions(parse_tree(files), keep_secrets=not cfg["secrets"])
            self._pulled = True
        db.set_setting("git_applied", head)
        return bool(changes)

    def _commit(self, root: Path, cfg: dict[str, Any], force: bool, ident: list[str]) -> bool:
        """Write the project to the folder and commit what changed. True when changes are left
        uncommitted (automatic commit is off)."""
        write_tree(root, build_tree(self._payload(), cfg["secrets"]))
        # only what pyhttp wrote: the repository may hold other work
        changed = _changed_paths(_run(root, "status", "--porcelain", "-z", "--untracked-files=all", "--", *MANAGED)[1])
        if not changed or not (cfg["auto_commit"] or force):
            return bool(changed)
        level = db.get_setting("git_applied") in (_head(root), "none")
        paths = _present(_run(root, "rev-parse", "--show-prefix")[1], changed)
        _run(root, "add", "-A", "--", *paths)
        # a file that differs only in line endings (a checkout with autocrlf) shows as changed
        # until it is staged: commit only when something really is
        if _run(root, "diff", "--cached", "--quiet", "--", *paths, check=False)[0] != 0:
            _run(root, *ident, "commit", "-m", _message(changed), "--", *paths)
        if level:   # the project was level with HEAD and still is; otherwise _catch_up has work to do
            db.set_setting("git_applied", _head(root))
        return False

    def _merge(self, root: Path, up: str, ident: list[str], prefer: str) -> None:
        """Merge what the remote has. A clean merge needs nobody; when both sides changed the same
        thing the merge is undone and reported, and ``prefer`` repeats it taking one side."""
        side = {"mine": ["-X", "ours"], "theirs": ["-X", "theirs"]}.get(prefer, [])
        if _run(root, *ident, "merge", "--no-edit", *side, up, check=False)[0] == 0:
            return
        both = _run(root, "diff", "--name-only", "--diff-filter=U", "--relative", "-z", check=False)[1].split("\0")
        names = [Path(p).parent.name if Path(p).stem == FOLDER_FILE else Path(p).stem for p in both if p]
        _run(root, "merge", "--abort", check=False)
        if not names:   # not a content conflict: unrelated histories, local changes in the way…
            raise GitError(f"Could not merge the changes from {up}. Run “git merge {up}” in the folder to see why, "
                           "fix it there and sync again.")
        what = ", ".join(names[:5]) + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")
        if side:
            raise GitError(f"The changes to {what} could not be merged automatically. Resolve them with Git in the folder, then sync again.")
        raise Conflict("merge", f"The remote and this project both changed {what}. Choose which version to keep.")

    # --------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        cfg = self.config()
        st = self._state
        if not cfg["dir"]:
            state = "off"
        elif st.get("syncing"):
            state = "syncing"
        elif self._timer is not None:
            state = "pending"
        elif st.get("error"):
            state = "error"
        else:
            state = "ok"
        return {**cfg, "state": state, "git": bool(shutil.which("git")), "error": st.get("error") or "",
                "synced_at": st.get("synced_at"), "pushed_at": st.get("pushed_at"), "commit": st.get("commit"),
                "dirty": bool(st.get("dirty")), "unpushed": bool(st.get("unpushed")),
                "remote_name": st.get("remote_name") or "", "repo": st.get("repo") or "", "branch": st.get("branch") or "",
                "conflict": st.get("conflict") or "", "pulled_at": st.get("pulled_at")}


def _head(root: Path) -> str:
    """The commit HEAD points at, or "" in a repository without commits."""
    code, out = _run(root, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return out.strip() if code == 0 else ""


def _upstream(root: Path, remote: str) -> str:
    """The remote branch this one follows: its configured upstream, else the branch of the same
    name on ``remote``; "" when the remote does not have it yet."""
    code, out = _run(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if code == 0 and out.strip():
        return out.strip()
    branch = _run(root, "branch", "--show-current", check=False)[1].strip()
    if branch and _run(root, "rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}", check=False)[0] == 0:
        return f"{remote}/{branch}"
    return ""
