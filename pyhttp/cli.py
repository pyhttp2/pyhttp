"""`pyhttp run`: run a collection headless, for CI (like Postman's newman).

    python -m pyhttp run                          # every request in your collection
    python -m pyhttp run "API/Users" Login        # folders (by name or path) and requests (by name or id)
    python -m pyhttp run collection.json          # a Postman / pyhttp / OpenAPI file, without importing it
    python -m pyhttp run --env QA --junit out/junit.xml --html out/report.html

Exit code: 0 when every request passed, 1 when any failed, 2 for usage errors.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import __version__, db, openapi, postman, runner


def _color(enabled: bool):
    def paint(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if enabled else text
    return paint


def build_parser(default_data_dir: Path) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pyhttp run", description="Run requests headless and report the results (for CI).")
    p.add_argument("targets", nargs="*", help="folder names or paths (A/B), request names or ids, or a collection "
                                               "file (Postman v2.1, pyhttp export, OpenAPI). Default: everything")
    p.add_argument("--data-dir", type=Path, default=default_data_dir, help=f"pyhttp data folder (default: {default_data_dir})")
    p.add_argument("-e", "--env", help="environment to use (name); default: the active one")
    p.add_argument("--env-file", type=Path, help="Postman environment file to use instead of a saved environment")
    p.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="set a global variable (repeatable)")
    p.add_argument("--tag", action="append", default=[], help="only requests with this tag (repeatable)")
    p.add_argument("-n", "--iterations", type=int, default=1, help="run everything N times")
    p.add_argument("-d", "--data", type=Path, help="CSV or JSON variables file: one row of variable values per iteration")
    p.add_argument("-P", "--parallel", type=int, nargs="?", const=8, default=0, metavar="N",
                   help="send everything at once, at most N in flight (default 8), instead of in order")
    p.add_argument("--bail", action="store_true", help="stop at the first failure")
    p.add_argument("--delay", type=float, default=0, metavar="MS", help="pause between requests")
    p.add_argument("--no-fail-on-http-error", action="store_true", help="4xx/5xx responses do not count as failures")
    p.add_argument("--no-fail-on-tests", action="store_true", help="failed tests do not count as failures")
    p.add_argument("--insecure", action="store_true", help="skip SSL certificate verification")
    p.add_argument("--timeout", type=float, help="default request timeout in seconds")
    p.add_argument("--junit", type=Path, help="write a JUnit XML report here")
    p.add_argument("--html", type=Path, help="write an HTML report here")
    p.add_argument("--json", type=Path, help="write the full results as JSON here")
    p.add_argument("--name", help="name of the run (default: the targets)")
    p.add_argument("-q", "--quiet", action="store_true", help="print only the summary")
    p.add_argument("--no-color", action="store_true", help="plain output")
    p.add_argument("--list", action="store_true", help="list what would run, then exit")
    return p


# ---------------------------------------------------------------- selection


def _ordered() -> list[dict[str, Any]]:
    """Every request in collection order: subfolders first, then the folder's own requests."""
    folders = db.list_folders()
    known = {f["id"] for f in folders}
    kids: dict[str, list[dict[str, Any]]] = {}
    for f in folders:
        kids.setdefault(f["parent_id"] if f["parent_id"] in known else "", []).append(f)
    reqs: dict[str, list[dict[str, Any]]] = {}
    for r in db.list_requests():
        reqs.setdefault(r["folder_id"] if r["folder_id"] in known else "", []).append(r)

    def walk(fid: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in kids.get(fid, []):
            out += walk(f["id"])
        return out + reqs.get(fid, [])
    return walk("")


def _folder_paths() -> dict[str, str]:
    folders = {f["id"]: f for f in db.list_folders()}
    paths: dict[str, str] = {}
    for fid in folders:
        chain, f, seen = [], folders.get(fid), set()
        while f and f["id"] not in seen:
            seen.add(f["id"])
            chain.append(f["name"])
            f = folders.get(f["parent_id"]) if f.get("parent_id") else None
        paths[fid] = "/".join(reversed(chain))
    return paths


def select(targets: list[str], tags: list[str]) -> list[dict[str, Any]]:
    """Requests matching the targets (in collection order, no duplicates). Raises ValueError for unknown targets."""
    ordered = _ordered()
    if not targets:
        chosen = ordered
    else:
        paths = _folder_paths()
        under: dict[str, set[str]] = {}  # folder id -> itself and descendants
        for fid, path in paths.items():
            under[fid] = {g for g, p in paths.items() if p == path or p.startswith(path + "/")}
        chosen_ids: list[str] = []
        for t in targets:
            tl = t.strip().strip("/").lower()
            folder_hits = [fid for fid, path in paths.items() if path.lower() == tl or path.lower().rsplit("/", 1)[-1] == tl]
            req_hits = [r["id"] for r in ordered if r["id"] == t or r["name"].lower() == tl]
            if not folder_hits and not req_hits:
                raise ValueError(f"no folder or request named “{t}”")
            fids = set().union(*(under[f] for f in folder_hits)) if folder_hits else set()
            for r in ordered:
                if (r["folder_id"] in fids or r["id"] in req_hits) and r["id"] not in chosen_ids:
                    chosen_ids.append(r["id"])
        by_id = {r["id"]: r for r in ordered}
        chosen = [by_id[i] for i in chosen_ids]
    if tags:
        chosen = [r for r in chosen if any(t in r["tags"] for t in tags)]
    return chosen


def load_file(path: Path) -> dict[str, Any]:
    """A collection file in pyhttp's import shape."""
    data = openapi.parse_text(path.read_text(encoding="utf-8-sig"))
    if postman.is_collection(data):
        return postman.from_collection(data)
    if openapi.is_openapi(data):
        return openapi.from_openapi(data)
    if openapi.is_har(data):
        return openapi.from_har(data)
    if isinstance(data, list):
        return {"folders": [], "requests": data, "environments": [], "variables": {}}
    if isinstance(data, dict) and ("requests" in data or "folders" in data):
        return {"folders": data.get("folders") or [], "requests": data.get("requests") or [],
                "environments": data.get("environments") or [], "variables": data.get("variables") or {}}
    raise ValueError(f"{path}: not a Postman collection, pyhttp export, OpenAPI spec or HAR file")


# ------------------------------------------------------------------ reports


def junit_xml(run: dict[str, Any]) -> str:
    results = run["results"]
    suites = ET.Element("testsuites", name=run["name"], tests=str(len(results)),
                        failures=str(sum(1 for r in results if r["status"] == "failed")),
                        time=f"{sum((r.get('duration_ms') or 0) for r in results) / 1000:.3f}")
    by_iter: dict[int, list[dict[str, Any]]] = {}
    for r in results:
        by_iter.setdefault(int(r.get("iteration") or 1), []).append(r)
    for it, items in by_iter.items():
        suite = ET.SubElement(suites, "testsuite", name=run["name"] + (f" (iteration {it})" if len(by_iter) > 1 else ""),
                              tests=str(len(items)), failures=str(sum(1 for r in items if r["status"] == "failed")),
                              time=f"{sum((r.get('duration_ms') or 0) for r in items) / 1000:.3f}")
        for r in items:
            name = r["request_name"] + (f" · {r['body_name']}" if r.get("body_name") else "")
            case = ET.SubElement(suite, "testcase", classname=run["name"], name=name,
                                 time=f"{(r.get('duration_ms') or 0) / 1000:.3f}")
            if r["status"] == "failed":
                problems = []
                if r.get("error"):
                    problems.append(r["error"])
                elif r.get("status_code") and r["status_code"] >= 400:
                    problems.append(f"HTTP {r['status_code']}")
                problems += [f"{t.get('name')}: {t.get('note') or 'failed'}" for t in r.get("tests") or [] if not t.get("passed")]
                fail = ET.SubElement(case, "failure", message=(problems[0] if problems else "failed")[:500])
                fail.text = "\n".join(problems)
            tests = r.get("tests") or []
            if tests:
                out = ET.SubElement(case, "system-out")
                out.text = "\n".join(f"[{'PASS' if t.get('passed') else 'FAIL'}] {t.get('name')}" + (f" — {t['note']}" if t.get("note") else "") for t in tests)
    ET.indent(suites)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suites, encoding="unicode")


def html_report(run: dict[str, Any]) -> str:
    e = html.escape
    rows = []
    for r in run["results"]:
        tests = "".join(f"<li class='{'ok' if t.get('passed') else 'bad'}'>{'✓' if t.get('passed') else '✗'} {e(str(t.get('name')))}"
                        f"{(' — ' + e(str(t['note']))) if t.get('note') else ''}</li>" for t in r.get("tests") or [])
        rows.append(
            f"<tr class='{e(r['status'])}'><td>{r['position']}</td><td>{e(r.get('method') or '')}</td>"
            f"<td>{e(r['request_name'])}{(' · ' + e(r['body_name'])) if r.get('body_name') else ''}"
            f"{f'<ul>{tests}</ul>' if tests else ''}{(chr(10) + '<div class=bad>' + e(r['error']) + '</div>') if r.get('error') else ''}</td>"
            f"<td>{r.get('status_code') or '—'}</td><td>{(r.get('duration_ms') or 0):.0f} ms</td><td>{e(r['status'])}</td></tr>")
    passed = sum(1 for r in run["results"] if r["status"] != "failed")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(run['name'])} — pyhttp report</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#1f2328}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}}tr.failed td{{background:#fff1f0}}
.ok{{color:#1a7f37}}.bad{{color:#cf222e}}ul{{margin:4px 0 0;padding-left:18px;font-size:12px}}.sum{{font-size:16px;margin:8px 0 16px}}</style>
</head><body><h1>{e(run['name'])}</h1>
<div class="sum">{passed}/{len(run['results'])} passed · {run['status']} · pyhttp {__version__} · {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(run['started_at']))}</div>
<table><tr><th>#</th><th>Method</th><th>Request</th><th>Status</th><th>Time</th><th>Result</th></tr>{''.join(rows)}</table></body></html>"""


# --------------------------------------------------------------------- main


def main(argv: list[str], default_data_dir: Path) -> int:
    args = build_parser(default_data_dir).parse_args(argv)
    paint = _color(not args.no_color and sys.stdout.isatty())
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    files = [Path(t) for t in args.targets if Path(t).is_file() and Path(t).suffix.lower() in (".json", ".yaml", ".yml", ".har")]
    tmp: tempfile.TemporaryDirectory[str] | None = None
    if files:
        # run straight from files: a throwaway data folder, the user's collection is not touched
        tmp = tempfile.TemporaryDirectory(prefix="pyhttp-run-", ignore_cleanup_errors=True)
        data_dir = Path(tmp.name)
        targets = [t for t in args.targets if Path(t) not in files]
        args.targets_from_files = True
    else:
        data_dir = args.data_dir.resolve()
        targets = list(args.targets)
        args.targets_from_files = False
        if not (data_dir / "pyhttp.db").exists():
            print(f"No pyhttp data in {data_dir} (use --data-dir, or pass a collection file)", file=sys.stderr)
            return 2
    try:
        db.init(data_dir / "pyhttp.db")
        if files:
            from .app import import_collection
            for f in files:
                conv = load_file(f)
                import_collection(conv["folders"], conv["requests"], conv.get("environments") or [],
                                  conv.get("variables") or {}, False)
        return _run(args, targets, data_dir, paint)
    except ValueError as exc:
        print(f"pyhttp run: {exc}", file=sys.stderr)
        return 2
    finally:
        db.close()
        if tmp:
            tmp.cleanup()


def _run(args: argparse.Namespace, targets: list[str], data_dir: Path, paint) -> int:
    # environment, variables and settings for this run (restored afterwards when using the user's data)
    restore: dict[str, Any] = {"active_env": db.get_setting("active_env"), "settings": db.app_settings(),
                               "variables": db.get_variables()}
    temp_env_id = None
    try:
        if args.env_file:
            env = postman.from_environment(openapi.parse_text(args.env_file.read_text(encoding="utf-8-sig")))
            temp_env_id = db.create_environment(env["name"] + " (pyhttp run)", env["variables"])["id"]
            db.set_setting("active_env", temp_env_id)
        elif args.env:
            match = [e for e in db.list_environments() if e["name"].lower() == args.env.lower() or e["id"] == args.env]
            if not match:
                names = ", ".join(e["name"] for e in db.list_environments()) or "none"
                raise ValueError(f"no environment “{args.env}” (have: {names})")
            db.set_setting("active_env", match[0]["id"])
        if args.var:
            values = db.get_variables()
            for kv in args.var:
                k, sep, v = kv.partition("=")
                if not sep or not k.strip():
                    raise ValueError(f"--var expects KEY=VALUE, got “{kv}”")
                values[k.strip()] = v
            db.set_variables(values)
        patch: dict[str, Any] = {}
        if args.insecure:
            patch["verify_ssl"] = False
        if args.timeout:
            patch["default_timeout"] = args.timeout
        if patch:
            db.update_settings(patch)

        chosen = select(targets, args.tag)
        if not chosen:
            raise ValueError("nothing to run")
        env = db.active_environment()
        if args.list:
            paths = _folder_paths()
            for r in chosen:
                where = paths.get(r["folder_id"] or "", "")
                print(f"{r['method']:7} {(where + '/') if where else ''}{r['name']}   {paint('2', r['url'])}")
            return 0

        data_rows: list[dict[str, Any]] = []
        if args.data:
            from .app import parse_data_file
            data_rows = parse_data_file(args.data.read_text(encoding="utf-8-sig"), args.data.name)
        name = args.name or (" + ".join(targets) if targets else "Collection run")
        options = {
            "name": name, "stop_on_error": args.bail, "fail_on_http_error": not args.no_fail_on_http_error,
            "fail_on_test": not args.no_fail_on_tests, "delay_ms": args.delay,
            "iterations": max(1, args.iterations), "data": data_rows, "source": "cli",
            "parallel": args.parallel > 0, "concurrency": args.parallel or 8,
        }
        if not args.quiet:
            print(paint("1", f"pyhttp {__version__} · {name}") + paint("2", f" · {len(chosen)} request(s)"
                  + (f" · environment {env['name']}" if env else "") + (f" · {max(1, args.iterations) if not data_rows else len(data_rows)} iterations" if (args.iterations > 1 or data_rows) else "")))
        run = asyncio.run(_execute(chosen, options, data_dir, args.quiet, paint))
    finally:
        if temp_env_id:
            db.delete_environment(temp_env_id)
        db.set_setting("active_env", restore["active_env"])
        if args.var:
            db.set_variables(restore["variables"])
        if args.insecure or args.timeout:
            db.update_settings({k: restore["settings"][k] for k in ("verify_ssl", "default_timeout")})

    results = run["results"]
    failed = sum(1 for r in results if r["status"] == "failed")
    tests = [t for r in results for t in r.get("tests") or []]
    tests_failed = sum(1 for t in tests if not t.get("passed"))
    total_ms = sum((r.get("duration_ms") or 0) for r in results)
    summary = (f"{len(results) - failed}/{len(results)} requests passed"
               + (f", {len(tests) - tests_failed}/{len(tests)} tests passed" if tests else "")
               + f" · {total_ms / 1000:.2f}s · {run['status']}")
    print(paint("32;1" if not failed and run["status"] == "completed" else "31;1", summary))
    if failed and not args.quiet:
        print(paint("31", "Failures:"))
        for r in results:
            if r["status"] == "failed":
                why = r.get("error") or (f"HTTP {r['status_code']}" if (r.get("status_code") or 0) >= 400 else "")
                bad = [t.get("name") for t in r.get("tests") or [] if not t.get("passed")]
                print(f"  {r['position']}. {r['request_name']}: " + "; ".join(x for x in [why, *bad] if x))
    for path, content in ((args.junit, junit_xml(run) if args.junit else None),
                          (args.html, html_report(run) if args.html else None),
                          (args.json, json.dumps(run, indent=2, ensure_ascii=False, default=str) if args.json else None)):
        if path and content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            if not args.quiet:
                print(paint("2", f"Report: {path}"))
    if not args.quiet and not args.targets_from_files:
        print(paint("2", f"Outputs: {run['output_dir']}"))
    return 1 if failed or run["status"] not in ("completed",) else 0


async def _execute(chosen: list[dict[str, Any]], options: dict[str, Any], data_dir: Path, quiet: bool, paint) -> dict[str, Any]:
    out_root = data_dir / "outputs"
    out_root.mkdir(parents=True, exist_ok=True)
    manager = runner.RunManager(out_root)
    run = await manager.start_saved([r["id"] for r in chosen], options)
    printed = 0
    while True:
        await asyncio.sleep(0.1)
        cur = db.get_run(run["id"])
        assert cur is not None
        if not quiet:
            for r in cur["results"][printed:]:
                ok = r["status"] != "failed"
                code = r.get("status_code")
                mark = paint("32", "✓") if ok else paint("31", "✗")
                label = r["request_name"] + (f" · {r['body_name']}" if r.get("body_name") else "")
                it = f"[{r.get('iteration')}] " if (options.get("iterations") or 1) > 1 or options.get("data") else ""
                print(f"{mark} {it}{r.get('method', ''):6} {label}  " + paint("2", f"{code or 'ERR'} · {(r.get('duration_ms') or 0):.0f} ms"))
                if r.get("error"):
                    print("    " + paint("31", r["error"]))
                for t in r.get("tests") or []:
                    if not t.get("passed"):
                        print("    " + paint("31", f"✗ {t.get('name')}") + (paint("2", f" — {t['note']}") if t.get("note") else ""))
            printed = len(cur["results"])
        if not manager.is_active(run["id"]) and cur["status"] != "running":
            return cur
