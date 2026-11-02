"""Postman Collection v2.1 and Postman environment conversion.

`from_collection()` turns a Postman collection into the same shape as pyhttp's
own export ({folders, requests, variables}); `to_collection()` does the
reverse so a pyhttp collection can be opened in Postman. Scripts (pre-request
and test JavaScript) cannot run here; they are kept in the request description
so nothing is silently lost.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"


def is_collection(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("info"), dict) and "item" in data


def is_environment(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("values"), list) and "name" in data and "item" not in data


# ------------------------------------------------------------------ import


def _kv(items: Any, key_name: str = "key") -> list[dict[str, Any]]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({"key": str(it.get(key_name) or ""), "value": "" if it.get("value") is None else str(it.get("value")),
                    "enabled": not it.get("disabled", False)})
    return out


def _url(u: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(u, str):
        return u, []
    if not isinstance(u, dict):
        return "", []
    raw = u.get("raw") or ""
    if not raw:
        host = u.get("host")
        host = ".".join(host) if isinstance(host, list) else (host or "")
        path = u.get("path")
        path = "/".join(path) if isinstance(path, list) else (path or "")
        proto = u.get("protocol")
        raw = (f"{proto}://" if proto else "") + host + (f":{u['port']}" if u.get("port") else "") + ("/" + path.lstrip("/") if path else "")
    params = _kv(u.get("query"))
    if params and "?" in raw:
        raw = raw.split("?", 1)[0]
    return raw, params


def _auth(a: Any) -> dict[str, Any]:
    if not isinstance(a, dict) or not a.get("type"):
        return {"type": "inherit"}
    t = a["type"]

    def vals(name: str) -> dict[str, str]:
        rows = a.get(name)
        if isinstance(rows, list):
            return {str(r.get("key")): str(r.get("value") if r.get("value") is not None else "") for r in rows if isinstance(r, dict)}
        if isinstance(rows, dict):
            return {k: str(v) for k, v in rows.items()}
        return {}

    if t == "noauth":
        return {"type": "none"}
    if t == "basic":
        v = vals("basic")
        return {"type": "basic", "username": v.get("username", ""), "password": v.get("password", "")}
    if t == "bearer":
        v = vals("bearer")
        return {"type": "bearer", "token": v.get("token", ""), "prefix": "Bearer"}
    if t == "apikey":
        v = vals("apikey")
        return {"type": "apikey", "key": v.get("key", ""), "value": v.get("value", ""),
                "add_to": "query" if v.get("in") == "query" else "header"}
    if t == "digest":
        v = vals("digest")
        return {"type": "digest", "username": v.get("username", ""), "password": v.get("password", "")}
    if t == "oauth2":
        v = vals("oauth2")
        grant = v.get("grant_type", "client_credentials")
        if grant not in ("client_credentials", "password_credentials"):
            # authorization code / implicit need a browser callback: keep the token if there is one
            return {"type": "bearer", "token": v.get("accessToken", ""), "prefix": v.get("headerPrefix") or "Bearer"}
        return {"type": "oauth2", "grant": "password" if grant == "password_credentials" else "client_credentials",
                "token_url": v.get("accessTokenUrl", ""), "client_id": v.get("clientId", ""),
                "client_secret": v.get("clientSecret", ""), "scope": v.get("scope", ""),
                "username": v.get("username", ""), "password": v.get("password", ""),
                "audience": v.get("audience", ""), "prefix": v.get("headerPrefix") or "Bearer",
                "client_auth": "body" if v.get("client_authentication") == "body" else "basic"}
    return {"type": "inherit"}


def _body(b: Any) -> tuple[str, str]:
    if not isinstance(b, dict) or not b.get("mode"):
        return "none", ""
    mode = b["mode"]
    if mode == "raw":
        lang = ((b.get("options") or {}).get("raw") or {}).get("language", "")
        raw = b.get("raw") or ""
        if lang in ("json", "xml", "html", "javascript", "text"):
            return lang, raw
        return ("json" if raw.lstrip().startswith(("{", "[")) else "text"), raw
    if mode == "urlencoded":
        rows = b.get("urlencoded") or []
        return "form", "\n".join(("// " if r.get("disabled") else "") + f"{r.get('key', '')}: {r.get('value', '')}" for r in rows if isinstance(r, dict))
    if mode == "formdata":
        lines = []
        for r in b.get("formdata") or []:
            if not isinstance(r, dict):
                continue
            prefix = "// " if r.get("disabled") else ""
            if r.get("type") == "file":
                src = r.get("src")
                src = src[0] if isinstance(src, list) and src else (src or "")
                lines.append(f"{prefix}{r.get('key', '')}: @{src}")
            else:
                lines.append(f"{prefix}{r.get('key', '')}: {r.get('value', '')}")
        return "multipart", "\n".join(lines)
    if mode == "file":
        src = (b.get("file") or {}).get("src") or ""
        return "binary", src
    if mode == "graphql":
        g = b.get("graphql") or {}
        variables = g.get("variables") or "{}"
        try:
            variables = json.loads(variables) if isinstance(variables, str) else variables
        except ValueError:
            pass
        return "graphql", json.dumps({"query": g.get("query") or "", "variables": variables}, indent=2)
    return "none", ""


def _scripts(events: Any) -> dict[str, str]:
    """Pre-request and test scripts of an item; they run unchanged in pyhttp's JavaScript engine."""
    out = {"pre": "", "test": "", "lang": "js"}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        exec_ = (ev.get("script") or {}).get("exec")
        code = "\n".join(exec_) if isinstance(exec_, list) else (exec_ or "")
        if not code.strip():
            continue
        key = "pre" if ev.get("listen") == "prerequest" else "test"
        out[key] = (out[key] + "\n\n" + code).strip() if out[key] else code.strip()
    return out


def _scripts_for(r: dict[str, Any], body: dict[str, Any] | None) -> dict[str, Any]:
    """The request's scripts for one exported body: those for all bodies followed by the body's own."""
    sc = dict((r.get("options") or {}).get("scripts") or {})
    own = (sc.get("bodies") or {}).get(body.get("id") or "") if body else None
    if isinstance(own, dict):
        for key in ("pre", "test"):
            parts = [c for c in ((sc.get(key) or "").strip(), (own.get(key) or "").strip()) if c]
            sc[key] = "\n\n".join(parts)
    return sc


def _events(scripts: dict[str, Any] | None, extra_tests: list[str]) -> list[dict[str, Any]]:
    """Postman events for export: pyhttp's scripts (JavaScript as-is, Python as comments) plus translated tests."""
    scripts = scripts or {}
    python = scripts.get("lang") == "py"
    out = []
    for listen, key in (("prerequest", "pre"), ("test", "test")):
        code = (scripts.get(key) or "").strip()
        lines: list[str] = []
        if code:
            if python:
                lines = ["// pyhttp Python script (does not run in Postman):"] + ["// " + ln for ln in code.splitlines()]
            else:
                lines = code.splitlines()
        if key == "test" and extra_tests:
            lines += ([""] if lines else []) + extra_tests
        if lines:
            out.append({"listen": listen, "script": {"type": "text/javascript", "exec": lines}})
    return out


def from_collection(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a Postman v2.1 collection into pyhttp's import shape."""
    folders: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    root_name = (data.get("info") or {}).get("name") or "Imported collection"
    root_id = uuid.uuid4().hex
    folders.append({"id": root_id, "name": root_name, "parent_id": None, "description": _desc(data.get("info", {}).get("description")),
                    "auth": _auth(data.get("auth")), "headers": [], "variables": _kv(data.get("variable"))})

    def walk(items: Any, parent_id: str) -> None:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            if "item" in it:  # folder
                fid = uuid.uuid4().hex
                folders.append({"id": fid, "name": it.get("name") or "Folder", "parent_id": parent_id,
                                "description": _desc(it.get("description")), "auth": _auth(it.get("auth")),
                                "headers": [], "variables": _kv(it.get("variable"))})
                walk(it.get("item"), fid)
                continue
            req = it.get("request")
            if isinstance(req, str):
                req = {"url": req, "method": "GET"}
            if not isinstance(req, dict):
                continue
            url, params = _url(req.get("url"))
            body_type, body = _body(req.get("body"))
            desc = _desc(req.get("description") or it.get("description"))
            requests.append({
                "folder_id": parent_id, "name": it.get("name") or url or "Request",
                "description": desc, "tags": [], "method": (req.get("method") or "GET").upper(), "url": url,
                "params": params, "headers": _kv(req.get("header")), "body_type": body_type, "body": body,
                "bodies": [{"id": "default", "name": "Default", "body": body, "enabled": True}] if body else [],
                "auth": _auth(req.get("auth")), "tests": [], "timeout": 30,
                "options": {"scripts": _scripts(it.get("event"))},
            })

    walk(data.get("item"), root_id)
    return {"folders": folders, "requests": requests, "variables": {}, "environments": [], "name": root_name}


def _desc(d: Any) -> str:
    if isinstance(d, dict):
        return str(d.get("content") or "")
    return str(d or "")


def from_environment(data: dict[str, Any]) -> dict[str, Any]:
    return {"name": data.get("name") or "Imported environment",
            "variables": [{"key": str(v.get("key") or ""), "value": "" if v.get("value") is None else str(v.get("value")),
                           "enabled": v.get("enabled", True) is not False, **({"secret": True} if v.get("type") == "secret" else {})}
                          for v in data.get("values") or [] if isinstance(v, dict)]}


# ------------------------------------------------------------------ export


def _pm_auth(a: dict[str, Any] | None) -> dict[str, Any] | None:
    a = a or {}
    t = a.get("type", "inherit")
    if t == "inherit":
        return None
    if t == "none":
        return {"type": "noauth"}
    if t == "basic":
        return {"type": "basic", "basic": [{"key": "username", "value": a.get("username", ""), "type": "string"},
                                           {"key": "password", "value": a.get("password", ""), "type": "string"}]}
    if t == "bearer":
        return {"type": "bearer", "bearer": [{"key": "token", "value": a.get("token", ""), "type": "string"}]}
    if t == "apikey":
        return {"type": "apikey", "apikey": [{"key": "key", "value": a.get("key", ""), "type": "string"},
                                             {"key": "value", "value": a.get("value", ""), "type": "string"},
                                             {"key": "in", "value": a.get("add_to", "header"), "type": "string"}]}
    if t == "digest":
        return {"type": "digest", "digest": [{"key": "username", "value": a.get("username", ""), "type": "string"},
                                             {"key": "password", "value": a.get("password", ""), "type": "string"}]}
    if t == "oauth2":
        fields = {
            "grant_type": "password_credentials" if a.get("grant") == "password" else "client_credentials",
            "accessTokenUrl": a.get("token_url", ""), "clientId": a.get("client_id", ""),
            "clientSecret": a.get("client_secret", ""), "scope": a.get("scope", ""),
            "client_authentication": "body" if a.get("client_auth") == "body" else "header",
            "headerPrefix": a.get("prefix") or "Bearer", "addTokenTo": "header",
        }
        if a.get("grant") == "password":
            fields.update(username=a.get("username", ""), password=a.get("password", ""))
        if a.get("audience"):
            fields["audience"] = a["audience"]
        return {"type": "oauth2", "oauth2": [{"key": k, "value": v, "type": "string"} for k, v in fields.items()]}
    return None


def _pm_kv(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"key": r.get("key", ""), "value": r.get("value", ""), **({"disabled": True} if not r.get("enabled", True) else {})}
            for r in rows or []]


def _form_rows(text: str) -> list[dict[str, Any]]:
    from .runner import parse_form_lines

    out = []
    for r in parse_form_lines(text):
        row: dict[str, Any] = {"key": r["key"], "type": "file" if r["file"] else "text"}
        if r["file"]:
            row["src"] = r["value"]
        else:
            row["value"] = r["value"]
        if not r["enabled"]:
            row["disabled"] = True
        out.append(row)
    return out


def _pm_body(r: dict[str, Any], body: str) -> dict[str, Any] | None:
    t = r.get("body_type", "none")
    if t == "none":
        return None
    if t in ("json", "text", "xml", "html", "javascript"):
        return {"mode": "raw", "raw": body, "options": {"raw": {"language": t}}}
    if t == "form":
        return {"mode": "urlencoded", "urlencoded": [{k: v for k, v in row.items() if k != "type"} for row in _form_rows(body)]}
    if t == "multipart":
        return {"mode": "formdata", "formdata": _form_rows(body)}
    if t == "binary":
        return {"mode": "file", "file": {"src": body.strip()}}
    if t == "graphql":
        try:
            d = json.loads(body)
            v = d.get("variables")
            return {"mode": "graphql", "graphql": {"query": d.get("query", ""), "variables": v if isinstance(v, str) else json.dumps(v or {})}}
        except (ValueError, AttributeError):
            return {"mode": "graphql", "graphql": {"query": body, "variables": "{}"}}
    return None


def _pm_tests(tests: list[dict[str, Any]]) -> list[str]:
    from .assertions import describe

    lines = []
    for t in tests or []:
        if not t.get("enabled", True):
            continue
        name = (t.get("name") or describe(t)).replace('"', '\\"')
        kind, op, val, target = t.get("type"), t.get("op"), t.get("value", ""), t.get("target", "")
        if kind == "status" and op == "equals":
            lines.append(f'pm.test("{name}", function () {{ pm.response.to.have.status({val}); }});')
        elif kind == "time" and op == "less_than":
            lines.append(f'pm.test("{name}", function () {{ pm.expect(pm.response.responseTime).to.be.below({val}); }});')
        elif kind == "header" and op == "exists":
            lines.append(f'pm.test("{name}", function () {{ pm.response.to.have.header("{target}"); }});')
        elif kind == "body" and op == "contains":
            lines.append(f'pm.test("{name}", function () {{ pm.expect(pm.response.text()).to.include({json.dumps(val)}); }});')
        elif kind == "json" and op == "equals":
            lines.append(f'pm.test("{name}", function () {{ pm.expect(String(pm.response.json().{target})).to.eql({json.dumps(str(val))}); }});')
        elif kind == "json" and op == "exists":
            lines.append(f'pm.test("{name}", function () {{ pm.expect(pm.response.json().{target}).to.not.be.undefined; }});')
        else:
            lines.append(f"// pyhttp test not translated: {name}")
    return lines


def to_collection(name: str, folders: list[dict[str, Any]], requests: list[dict[str, Any]],
                  variables: dict[str, str]) -> dict[str, Any]:
    """Build a Postman v2.1 collection. Each named body becomes its own request."""
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for f in folders:
        by_parent.setdefault(f.get("parent_id"), []).append(f)
    reqs_by_folder: dict[str | None, list[dict[str, Any]]] = {}
    for r in requests:
        reqs_by_folder.setdefault(r.get("folder_id"), []).append(r)

    def request_items(r: dict[str, Any]) -> list[dict[str, Any]]:
        bodies = [b for b in r.get("bodies") or [] if r.get("body_type", "none") != "none"] or [None]
        items = []
        for b in bodies:
            body_text = b["body"] if b else ""
            label = r["name"] if (b is None or len(bodies) == 1) else f"{r['name']} · {b['name']}"
            url = r.get("url", "")
            params = [p for p in r.get("params") or [] if p.get("key")]
            pm_url: dict[str, Any] = {"raw": url + ("?" + "&".join(f"{p['key']}={p['value']}" for p in params if p.get("enabled", True)) if params else "")}
            if params:
                pm_url["query"] = _pm_kv(params)
            req: dict[str, Any] = {"method": r.get("method", "GET"), "header": _pm_kv(r.get("headers") or []), "url": pm_url}
            if r.get("description"):
                req["description"] = r["description"]
            auth = _pm_auth(r.get("auth"))
            if auth:
                req["auth"] = auth
            body = _pm_body(r, body_text)
            if body:
                req["body"] = body
            item: dict[str, Any] = {"name": label, "request": req, "response": []}
            events = _events(_scripts_for(r, b), _pm_tests(r.get("tests") or []))
            if events:
                item["event"] = events
            items.append(item)
        return items

    def walk(parent: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in by_parent.get(parent, []):
            node: dict[str, Any] = {"name": f["name"], "item": walk(f["id"])}
            if f.get("description"):
                node["description"] = f["description"]
            auth = _pm_auth(f.get("auth"))
            if auth:
                node["auth"] = auth
            if f.get("variables"):
                node["variable"] = _pm_kv(f["variables"])
            out.append(node)
        for r in reqs_by_folder.get(parent, []):
            out.extend(request_items(r))
        return out

    return {
        "info": {"_postman_id": str(uuid.uuid4()), "name": name, "schema": SCHEMA},
        "item": walk(None),
        "variable": [{"key": k, "value": v, "type": "string"} for k, v in (variables or {}).items()],
    }


def to_environment(env: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "name": env["name"], "_postman_variable_scope": "environment",
            "values": [{"key": v["key"], "value": v["value"], "enabled": v.get("enabled", True),
                        "type": "secret" if v.get("secret") else "default"}
                       for v in env.get("variables") or []]}
