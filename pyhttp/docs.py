"""Collection documentation as Markdown: folders, requests, descriptions, example bodies, auth,
tests. Secrets never appear (header values that look like credentials are masked)."""
from __future__ import annotations

import json
import re
from typing import Any

_SENSITIVE = re.compile(r"authorization|api[-_]?key|token|secret|password|cookie", re.I)


def _mask(key: str, value: str) -> str:
    if _SENSITIVE.search(key or "") and value and not re.fullmatch(r"\{\{[^}]+\}\}", value.strip()):
        return "••••••"
    return value


def _code(text: str, lang: str = "") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}\n"


def _pretty_body(body: str, body_type: str) -> tuple[str, str]:
    if body_type == "json":
        try:
            return json.dumps(json.loads(body), indent=2, ensure_ascii=False), "json"
        except ValueError:
            return body, "json"
    if body_type == "graphql":
        try:
            d = json.loads(body)
            vars_ = d.get("variables")
            out = str(d.get("query") or "")
            if vars_ and vars_ not in ("{}", {}):
                out.extend("\n\n# variables\n" + (vars_ if isinstance(vars_, str) else json.dumps(vars_, indent=2)))
            return out, "graphql"
        except ValueError:
            return body, "graphql"
    return body, {"xml": "xml", "html": "html", "javascript": "javascript", "form": "", "multipart": "", "text": ""}.get(body_type, "")


def _auth_line(auth: dict[str, Any]) -> str:
    t = (auth or {}).get("type") or "inherit"
    names = {"inherit": "inherited from the folder", "none": "none", "basic": "Basic", "bearer": "Bearer token",
             "apikey": "API key", "digest": "Digest", "oauth2": "OAuth 2.0"}
    line = names.get(t, t)
    if t == "apikey":
        line += f" (`{auth.get('key') or 'X-API-Key'}` in the {'query string' if auth.get('add_to') == 'query' else 'header'})"
    if t == "oauth2":
        line += f" ({'password' if auth.get('grant') == 'password' else 'client credentials'} grant)"
    return line


def _test_line(t: dict[str, Any]) -> str:
    kind, op, target, value = t.get("type", ""), t.get("op", ""), t.get("target", ""), t.get("value", "")
    subject = {"status": "status code", "header": f"header `{target}`", "body": "body", "json": f"`{target}`",
               "time": "response time (ms)"}.get(kind, kind)
    return f"{subject} {op} `{value}`".strip()


def _anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def to_markdown(title: str, folders: list[dict[str, Any]], requests: list[dict[str, Any]],
                variables: dict[str, str] | None = None, environments: list[dict[str, Any]] | None = None) -> str:
    kids: dict[str | None, list[dict[str, Any]]] = {}
    for f in folders:
        kids.setdefault(f.get("parent_id") or None, []).append(f)
    reqs: dict[str | None, list[dict[str, Any]]] = {}
    for r in requests:
        reqs.setdefault(r.get("folder_id") or None, []).append(r)
    for lst in (*kids.values(), *reqs.values()):
        lst.sort(key=lambda x: (x.get("position") or 0, str(x.get("name") or "").lower()))

    out: list[str] = [f"# {title}", ""]
    n_req = len(requests)
    out.append(f"{n_req} request{'s' if n_req != 1 else ''} in {len(folders)} folder{'s' if len(folders) != 1 else ''}.")
    out.append("")
    if variables:
        out.extend(["## Variables", "", "| Name | Default |", "|---|---|"])
        for k, v in sorted(variables.items()):
            out.append(f"| `{k}` | `{_mask(k, v)}` |")
        out.append("")
    if environments:
        out.extend(["## Environments", ""])
        for e in environments:
            names = ", ".join(f"`{v.get('key')}`" for v in (e.get("variables") or []) if v.get("key"))
            out.append(f"- **{e.get('name')}**" + (f": {names}" if names else ""))
        out.append("")

    def toc(fid: str | None, depth: int) -> None:
        for f in kids.get(fid, []):
            out.append("  " * depth + f"- [{f['name']}](#{_anchor(f['name'])})")
            toc(f["id"], depth + 1)
        for r in reqs.get(fid, []):
            out.append("  " * depth + f"- `{r.get('method', 'GET')}` [{r.get('name')}](#{_anchor(r.get('name') or '')})")

    out.extend(["## Contents", ""])
    toc(None, 0)
    out.append("")

    def request_md(r: dict[str, Any], level: int) -> None:
        out.append(f"{'#' * min(level, 6)} {r.get('name') or 'Untitled'}")
        out.append("")
        out.append(f"`{r.get('method', 'GET')}` `{r.get('url') or ''}`")
        out.append("")
        if r.get("tags"):
            out.append("Tags: " + ", ".join(f"`{t}`" for t in r["tags"]))
            out.append("")
        if (r.get("description") or "").strip():
            out.append(r["description"].strip())
            out.append("")
        params = [p for p in r.get("params") or [] if p.get("enabled", True) and p.get("key")]
        if params:
            out.extend(["**Query parameters**", "", "| Name | Value |", "|---|---|"])
            out.extend([f"| `{p['key']}` | `{p.get('value', '')}` |" for p in params])
            out.append("")
        headers = [h for h in r.get("headers") or [] if h.get("enabled", True) and h.get("key")]
        if headers:
            out.extend(["**Headers**", "", "| Name | Value |", "|---|---|"])
            out.extend([f"| `{h['key']}` | `{_mask(h['key'], h.get('value', ''))}` |" for h in headers])
            out.append("")
        auth = r.get("auth") or {}
        if auth.get("type") and auth["type"] != "inherit":
            out.append(f"**Auth**: {_auth_line(auth)}")
            out.append("")
        bt = r.get("body_type") or "none"
        bodies = [b for b in r.get("bodies") or [] if (b.get("body") or "").strip()]
        if bt != "none" and bodies:
            out.append(f"**Body** ({bt})" if len(bodies) == 1 else f"**Bodies** ({bt}, {len(bodies)} examples)")
            out.append("")
            for b in bodies:
                if len(bodies) > 1:
                    out.append(f"*{b.get('name') or 'Unnamed'}*" + ("" if b.get("enabled", True) else " (unchecked)"))
                    out.append("")
                text, lang = _pretty_body(b.get("body") or "", bt)
                out.append(_code(text, lang))
        tests = r.get("tests") or []
        if tests:
            out.append("**Tests**")
            out.append("")
            out.extend([f"- {_test_line(t)}" for t in tests])
            out.append("")
        dep = ((r.get("options") or {}).get("depends") or {})
        if dep.get("request_id"):
            name = next((x.get("name") for x in requests if x.get("id") == dep["request_id"]), None)
            if name:
                out.append(f"Sends **{name}** first.")
                out.append("")

    def folder_md(fid: str | None, level: int) -> None:
        for f in kids.get(fid, []):
            out.append(f"{'#' * min(level, 6)} {f['name']}")
            out.append("")
            if (f.get("description") or "").strip():
                out.append(f["description"].strip())
                out.append("")
            fa = f.get("auth") or {}
            if fa.get("type") and fa["type"] not in ("inherit", "none"):
                out.append(f"Auth for every request inside: {_auth_line(fa)}")
                out.append("")
            fh = [h for h in f.get("headers") or [] if h.get("enabled", True) and h.get("key")]
            if fh:
                out.append("Headers added to every request inside: " + ", ".join(f"`{h['key']}`" for h in fh))
                out.append("")
            for r in reqs.get(f["id"], []):
                request_md(r, level + 1)
            folder_md(f["id"], level + 1)

    folder_md(None, 2)
    for r in reqs.get(None, []):
        request_md(r, 2)
    return "\n".join(out).rstrip() + "\n"
