"""Maintenance commands: ``pyhttp projects …`` and ``pyhttp doctor``.

Both work on the data folder directly (no running app needed) and print plain text, so they
are usable from scripts and CI as well as by hand.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from . import db, projects

# ------------------------------------------------------------------ helpers


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _dir_size(path: Path) -> tuple[int, int]:
    """(bytes, number of entries) under a folder; missing folder → (0, 0)."""
    total, count = 0, 0
    if not path.exists():
        return 0, 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
                count += 1
        except OSError:
            continue
    return total, count


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{question} — refusing without --yes when not interactive", file=sys.stderr)
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# ------------------------------------------------------------------ projects


def projects_main(argv: list[str], default_data_dir: Path) -> int:
    p = argparse.ArgumentParser(prog="pyhttp projects", description="List, create, switch, rename and delete projects (workspaces).")
    p.add_argument("--data-dir", type=Path, default=default_data_dir, help=f"pyhttp data folder (default: {default_data_dir})")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list projects (default)")
    c = sub.add_parser("create", help="create a project (app settings are copied from the active one)")
    c.add_argument("name")
    c.add_argument("--use", action="store_true", help="also make it the active project")
    u = sub.add_parser("use", help="make a project the active one (the app and the CLI runner open it next)")
    u.add_argument("name", help="project name or id")
    r = sub.add_parser("rename", help="rename a project")
    r.add_argument("name", help="current name or id")
    r.add_argument("new_name")
    d = sub.add_parser("delete", help="delete a project and all its files")
    d.add_argument("name", help="project name or id")
    d.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = p.parse_args(argv)
    data_dir = args.data_dir.resolve()
    reg = projects.Registry(data_dir)

    def find(name: str) -> dict[str, Any]:
        found = reg.find(name)
        if found is None:
            names = ", ".join(x["name"] for x in reg.list())
            raise ValueError(f"no project called {name!r} (projects: {names})")
        return found

    try:
        if args.cmd in (None, "list"):
            for x in reg.list():
                mark = "*" if x["active"] else " "
                print(f"{mark} {x['name']:<30} {x['id']:<40} {_fmt_bytes(x['db_size']):>9}  {x['dir']}")
            return 0
        if args.cmd == "create":
            active_db = reg.paths(reg.active())[0]
            settings: dict[str, str] = {}
            if active_db.exists():
                db.init(active_db)
                settings = db.app_settings_rows()
                db.close()
            proj = reg.create(args.name)
            db.seed_database(reg.paths(proj["id"])[0], settings)
            if args.use:
                reg.set_active(proj["id"])
            print(f"Created project “{proj['name']}” ({proj['id']}){' and made it active' if args.use else ''}")
            return 0
        if args.cmd == "use":
            proj = find(args.name)
            reg.set_active(proj["id"])
            print(f"Active project: {proj['name']}")
            return 0
        if args.cmd == "rename":
            proj = find(args.name)
            reg.rename(proj["id"], args.new_name)
            print(f"Renamed “{proj['name']}” to “{args.new_name}”")
            return 0
        if args.cmd == "delete":
            proj = find(args.name)
            if proj["id"] == reg.active():
                print("That is the active project: switch with `pyhttp projects use <other>` first", file=sys.stderr)
                return 2
            if not _confirm(f"Delete project “{proj['name']}” and all its files?", args.yes):
                return 1
            reg.delete(proj["id"])
            print(f"Deleted project “{proj['name']}”")
            return 0
    except ValueError as exc:
        print(f"pyhttp projects: {exc}", file=sys.stderr)
        return 2
    return 0


# -------------------------------------------------------------------- doctor


def _inspect_db(path: Path) -> dict[str, Any]:
    """Row counts and an integrity check, on a private read-only connection."""
    out: dict[str, Any] = {"ok": True, "integrity": "ok", "counts": {}}
    if not path.exists():
        return {"ok": False, "integrity": "missing", "counts": {}}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"ok": False, "integrity": f"cannot open: {exc}", "counts": {}}
    try:
        res = conn.execute("PRAGMA quick_check").fetchone()
        out["integrity"] = str(res[0]) if res else "unknown"
        out["ok"] = out["integrity"] == "ok"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for t in ("requests", "folders", "environments", "history", "runs", "mock_servers", "monitors", "cookies"):
            if t in tables:
                out["counts"][t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if "runs" in tables:
            out["run_dirs"] = {r[0] for r in conn.execute("SELECT output_dir FROM runs")}
    except sqlite3.Error as exc:
        out.update({"ok": False, "integrity": f"error: {exc}"})
    finally:
        conn.close()
    return out


def doctor_main(argv: list[str], default_data_dir: Path) -> int:
    p = argparse.ArgumentParser(prog="pyhttp doctor", description="Check the data folder: databases, run outputs, disk use.")
    p.add_argument("--data-dir", type=Path, default=default_data_dir, help=f"pyhttp data folder (default: {default_data_dir})")
    p.add_argument("--clean-outputs", type=float, metavar="DAYS", help="delete run output folders older than DAYS (in every project)")
    p.add_argument("--yes", action="store_true", help="do not ask before deleting")
    args = p.parse_args(argv)
    data_dir = args.data_dir.resolve()
    print(f"Data folder: {data_dir}" + ("" if data_dir.exists() else "  (does not exist yet)"))
    if data_dir.exists():
        try:
            du = shutil.disk_usage(data_dir)
            print(f"Disk: {_fmt_bytes(du.free)} free of {_fmt_bytes(du.total)}")
        except OSError:
            pass
    reg = projects.Registry(data_dir)
    problems = 0
    to_delete: list[Path] = []
    cutoff = time.time() - args.clean_outputs * 86400 if args.clean_outputs is not None else None
    for proj in reg.list():
        db_path, out_dir = reg.paths(proj["id"])
        info = _inspect_db(db_path)
        wal = Path(str(db_path) + "-wal")
        wal_note = f", WAL {_fmt_bytes(wal.stat().st_size)}" if wal.exists() else ""
        print(f"\nProject “{proj['name']}”{' (active)' if proj['active'] else ''}  —  {db_path.parent}")
        print(f"  database: {_fmt_bytes(proj['db_size'])}{wal_note}, integrity {info['integrity']}")
        if not info["ok"]:
            problems += 1
        if info["counts"]:
            print("  rows:     " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in info["counts"].items()))
        size, files = _dir_size(out_dir)
        folders = [d for d in out_dir.iterdir() if d.is_dir()] if out_dir.exists() else []
        print(f"  outputs:  {len(folders)} run folder(s), {files} file(s), {_fmt_bytes(size)}")
        known = {str(Path(x).resolve()) for x in info.get("run_dirs", set())}
        orphans = [d for d in folders if str(d.resolve()) not in known]
        if orphans:
            print(f"  orphans:  {len(orphans)} folder(s) no run refers to any more (safe to delete)")
        if cutoff is not None:
            old = [d for d in folders if d.stat().st_mtime < cutoff]
            to_delete.extend(old)
            if old:
                print(f"  to clean: {len(old)} folder(s) older than {args.clean_outputs:g} day(s)")
    if cutoff is not None:
        if not to_delete:
            print("\nNothing to clean.")
        elif _confirm(f"\nDelete {len(to_delete)} run output folder(s)?", args.yes):
            freed = 0
            for d in to_delete:
                freed += _dir_size(d)[0]
                shutil.rmtree(d, ignore_errors=True)
            print(f"Deleted {len(to_delete)} folder(s), {_fmt_bytes(freed)} freed. Run history keeps its rows; the files are gone.")
    print("\nAll good." if not problems else f"\n{problems} problem(s) found.")
    return 1 if problems else 0
