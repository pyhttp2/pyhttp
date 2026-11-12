"""Executes HTTP requests and sequential runs, writing every response to disk."""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import time
from datetime import datetime
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import USER_AGENT, assertions, console, db, jsengine, oauth2, scripts
from .jsonc import strip_comments
from .templating import build_context, render, unresolved

MAX_STORED_BODY = 512_000  # chars of body text kept in SQLite; files always hold the full body
MAX_INLINE_BINARY = 5_000_000  # bytes of binary response shipped to the UI as base64 (images, PDFs)

_BODY_CONTENT_TYPES = {
    "json": "application/json",
    "text": "text/plain",
    "xml": "application/xml",
    "html": "text/html",
    "javascript": "application/javascript",
    "graphql": "application/json",
}
TEXT_BODY_TYPES = ("json", "text", "xml", "html", "javascript", "graphql")


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return (s or "request")[:60]


def _ext_for(content_type: str) -> str:
    ct = content_type.split(";")[0].strip().lower()
    if "json" in ct:
        return ".json"
    if "html" in ct:
        return ".html"
    if "xml" in ct:
        return ".xml"
    if ct.startswith("text/") or "javascript" in ct:
        return ".txt"
    if ct.startswith("image/"):
        return "." + ct.split("/")[1].split("+")[0]
    if ct == "application/pdf":
        return ".pdf"
    return ".bin"


def _enabled_pairs(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for r in rows or []:
        if not r.get("enabled", True):
            continue
        key = render(r.get("key", ""), ctx).strip()
        if key:
            out.append((key, render(r.get("value", ""), ctx)))
    return out


def parse_form_lines(text: str) -> list[dict[str, Any]]:
    """`key: value` per line (or key=value); `// ` disables a line; `key: @path` is a file field."""
    rows: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        l = line.strip()
        if not l:
            continue
        enabled = True
        if l.startswith("//"):
            enabled, l = False, l[2:].strip()
        ci, ei = l.find(":"), l.find("=")
        i = ei if ei != -1 and (ci == -1 or ei < ci) else ci
        key = (l if i == -1 else l[:i]).strip()
        value = "" if i == -1 else l[i + 1:].strip()
        if not key and not value:
            continue
        is_file = value.startswith("@") and len(value) > 1
        rows.append({"key": key, "value": value[1:] if is_file else value, "file": is_file, "enabled": enabled})
    return rows


def _path_variables(url: str) -> list[str]:
    """`:id` style segments in the path part of a URL."""
    path = url.split("?", 1)[0]
    return re.findall(r"(?<=/):([A-Za-z_][A-Za-z0-9_]*)", path)


def _apply_path_variables(url: str, params: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    names = _path_variables(url)
    if not names:
        return url, params
    values = {k: v for k, v in params if k in names}
    rest = [(k, v) for k, v in params if k not in names]
    path, sep, query = url.partition("?")
    for n in names:
        if n in values:
            path = re.sub(rf"(?<=/):{re.escape(n)}(?=/|$)", values[n], path)
    return path + sep + query, rest


def _host_matches(host: str, pattern: str) -> bool:
    pattern = pattern.strip().lower()
    host = host.lower()
    if not pattern:
        return False
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) or host == pattern[2:]
    return host == pattern or host.endswith("." + pattern)


# ------------------------------------------------------------------ cookies


def _jar_from_db() -> CookieJar:
    jar = CookieJar()
    now = time.time()
    for c in db.list_cookies():
        if c.get("expires") and c["expires"] < now:
            continue
        jar.set_cookie(_cookie(c))
    return jar


def _cookie(c: dict[str, Any]) -> Cookie:
    domain = c["domain"]
    return Cookie(
        version=0, name=c["name"], value=c.get("value", ""), port=None, port_specified=False,
        domain=domain, domain_specified=bool(domain), domain_initial_dot=domain.startswith("."),
        path=c.get("path") or "/", path_specified=True, secure=bool(c.get("secure")),
        expires=int(c["expires"]) if c.get("expires") else None, discard=not c.get("expires"),
        comment=None, comment_url=None, rest={"HttpOnly": None} if c.get("http_only") else {}, rfc2109=False,
    )


def jar_to_list(jar: CookieJar) -> list[dict[str, Any]]:
    out = []
    for ck in jar:
        out.append({
            "name": ck.name, "value": ck.value or "", "domain": ck.domain, "path": ck.path,
            "expires": ck.expires, "secure": bool(ck.secure), "http_only": ck.has_nonstandard_attr("HttpOnly"),
        })
    return out


# ------------------------------------------------------------------ prepare


def apply_pre_vars(req: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Pre-request variables: evaluated before the request, saved to env/global when asked."""
    applied = []
    for row in req.get("pre_vars") or []:
        if not row.get("enabled", True) or not (row.get("key") or "").strip():
            continue
        name = row["key"].strip()
        value = render(row.get("value", ""), ctx)
        scope = row.get("scope") or "local"
        ctx["vars"][name] = value
        if scope in ("env", "global"):
            scope = db.set_variable(name, value, scope)
        else:
            ctx.setdefault("locals", {})[name] = value
        applied.append({"name": name, "value": value, "scope": scope})
    return applied


def prepare(req: dict[str, Any], ctx: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve everything that will be sent, without sending it.

    Returns {method, url, params, headers, body, body_kind, files, form, auth, follow_redirects,
    unresolved, auth_from, content_type}.
    """
    settings = settings or db.app_settings()
    method = (req.get("method") or "GET").upper()
    url = render(req.get("url", ""), ctx).strip()
    params = _enabled_pairs(req.get("params") or [], ctx)
    url, params = _apply_path_variables(url, params)
    inh = db.inherited(req.get("folder_id")) if req.get("folder_id") else {"auth": {"type": "none"}, "auth_from": None, "headers": []}

    headers: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for k, v in _enabled_pairs(inh["headers"], ctx) + _enabled_pairs(req.get("headers") or [], ctx):
        if k.lower() in seen:
            headers[seen[k.lower()]] = (k, v)
        else:
            seen[k.lower()] = len(headers)
            headers.append((k, v))

    def has_header(name: str) -> bool:
        return name.lower() in seen

    def add_header(name: str, value: str) -> None:
        seen[name.lower()] = len(headers)
        headers.append((name, value))

    # ---- auth
    auth = db.normalize_auth(req.get("auth"))
    auth_from = None
    if auth["type"] == "inherit":
        auth, auth_from = inh["auth"], inh["auth_from"]
    auth_raw = [v for k, v in auth.items() if isinstance(v, str) and k != "type"] if auth["type"] != "none" else []
    auth = {k: (render(v, ctx) if isinstance(v, str) else v) for k, v in auth.items()}
    digest: tuple[str, str] | None = None
    if auth["type"] == "basic":
        token = base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode()
        add_header("Authorization", f"Basic {token}")
    elif auth["type"] == "bearer":
        prefix = (auth.get("prefix") or "").strip()
        add_header("Authorization", f"{prefix} {auth['token']}".strip())
    elif auth["type"] == "apikey" and auth["key"]:
        if auth["add_to"] == "query":
            params.append((auth["key"], auth["value"]))
        else:
            add_header(auth["key"], auth["value"])
    elif auth["type"] == "digest":
        digest = (auth["username"], auth["password"])
    oauth: dict[str, Any] | None = None
    if auth["type"] == "oauth2" and not has_header("Authorization"):  # an explicit header wins
        oauth = auth
        tok = oauth2.cached(auth)
        prefix = (auth.get("prefix") or "").strip()
        # without a cached token the header is filled in by execute() after fetching one
        add_header("Authorization", f"{prefix} {tok['access_token'] if tok else '<OAuth 2.0 token>'}".strip())

    # ---- body
    body_type = req.get("body_type") or "none"
    raw = req.get("body") or ""
    body_text: str | None = None
    body_kind = "none"
    files: list[tuple[str, tuple[str, bytes, str | None]]] = []
    form: list[tuple[str, str]] = []
    body_bytes: bytes | None = None
    if body_type in TEXT_BODY_TYPES and raw.strip():
        text = strip_comments(raw) if body_type in ("json", "graphql") else raw
        body_text = render(text, ctx)
        if body_type == "graphql":
            body_text = _graphql_body(body_text)
        body_kind = "text"
        if not has_header("Content-Type"):
            add_header("Content-Type", _BODY_CONTENT_TYPES[body_type])
    elif body_type in ("form", "multipart"):
        rows = [r for r in parse_form_lines(raw) if r["enabled"]]
        for r in rows:
            key = render(r["key"], ctx)
            value = render(r["value"], ctx)
            if body_type == "multipart" and r["file"]:
                p = Path(value).expanduser()
                data = p.read_bytes() if p.is_file() else b""
                files.append((key, (p.name, data, mimetypes.guess_type(p.name)[0])))
            else:
                form.append((key, value))
        body_kind = "multipart" if body_type == "multipart" else "form"
        if body_kind == "form":
            body_text = "&".join(f"{httpx.QueryParams({k: v})}" for k, v in form)
            if not has_header("Content-Type"):
                add_header("Content-Type", "application/x-www-form-urlencoded")
    elif body_type == "binary" and raw.strip():
        p = Path(render(raw.strip(), ctx)).expanduser()
        body_bytes = p.read_bytes() if p.is_file() else None
        body_kind = "binary"
        body_text = f"<file: {p}>" + ("" if p.is_file() else " (not found)")
        if not has_header("Content-Type"):
            add_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")

    if not has_header("User-Agent"):
        add_header("User-Agent", (settings.get("user_agent") or "").strip() or USER_AGENT)

    options = req.get("options") or {}
    follow = options.get("follow_redirects")
    if follow is None:
        follow = settings.get("follow_redirects", True)

    checked = [req.get("url", ""), *auth_raw] + [r.get("key", "") + r.get("value", "") for r in (req.get("params") or []) + (req.get("headers") or []) if r.get("enabled", True)]
    if body_type in TEXT_BODY_TYPES:
        checked.append(strip_comments(raw) if body_type in ("json", "graphql") else raw)
    elif body_type in ("form", "multipart", "binary"):
        checked.append(raw)
    missing: list[str] = []
    for text in checked:
        for name in unresolved(text, ctx):
            if name not in missing:
                missing.append(name)

    return {
        "method": method, "url": url, "params": params, "headers": headers, "body": body_text,
        "body_kind": body_kind, "files": files, "form": form, "body_bytes": body_bytes,
        "digest": digest, "oauth2": oauth, "follow_redirects": bool(follow), "unresolved": missing,
        "auth_type": auth["type"], "auth_from": auth_from, "body_type": body_type,
        "send_cookies": bool(options.get("send_cookies", True)) and settings.get("send_cookies", True),
        "store_cookies": bool(options.get("store_cookies", True)),
    }


def _graphql_body(text: str) -> str:
    """A GraphQL body is stored as {"query": "...", "variables": ...}; variables may be a JSON string."""
    try:
        data = json.loads(text)
    except ValueError:
        return json.dumps({"query": text})
    if not isinstance(data, dict):
        return json.dumps({"query": text})
    v = data.get("variables")
    if isinstance(v, str):
        try:
            data["variables"] = json.loads(strip_comments(v)) if v.strip() else {}
        except ValueError:
            pass
    return json.dumps({"query": data.get("query", ""), "variables": data.get("variables") or {},
                       **({"operationName": data["operationName"]} if data.get("operationName") else {})})


def public_request(p: dict[str, Any]) -> dict[str, Any]:
    """The resolved request as shown to the UI (no bytes)."""
    out = {k: v for k, v in p.items() if k not in ("files", "body_bytes", "digest", "oauth2")}
    out["files"] = [{"field": name, "filename": fn, "size": len(data), "content_type": ct} for name, (fn, data, ct) in p.get("files", [])]
    out["has_digest"] = p.get("digest") is not None
    return out


# ------------------------------------------------------------------ execute


# ------------------------------------------------------------------ dependencies
# A request can depend on another one (e.g. "Get token"): that one is sent first, its extractions and
# scripts set the variables, and it is sent again only when its last result is older than the expiry.
_dep_locks: dict[str, asyncio.Lock] = {}
MAX_DEPENDENCY_DEPTH = 5


def _dependency_of(req: dict[str, Any]) -> dict[str, Any] | None:
    dep = (req.get("options") or {}).get("depends")
    return dep if isinstance(dep, dict) and dep.get("request_id") else None


async def _ensure_dependency(req: dict[str, Any], dep: dict[str, Any], ctx: dict[str, Any],
                             force: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Send the dependency unless its last result is still fresh. Returns (info for the UI, error result
    to return instead of sending `req`, or None). Parallel sends share one lock per dependency, so a
    token is fetched once, not once per body."""
    dep_id = str(dep["request_id"])
    dep_req = db.get_request(dep_id)
    if not dep_req:
        info = {"name": "(deleted request)", "state": "missing"}
        err = _error_result("The request this one depends on no longer exists (see its Settings tab)",
                            {"method": req.get("method"), "url": req.get("url"), "params": [], "headers": [], "body": None}, 0.0, req, [])
        return info, err
    chain = tuple(ctx.get("_dep_chain") or ()) + ((req.get("id") or "",))
    if dep_id in chain or len(chain) > MAX_DEPENDENCY_DEPTH:
        return {"name": dep_req["name"], "state": "loop"}, None   # A -> B -> A: do not recurse
    ttl = max(0, int(dep.get("ttl") or 0))
    lock = _dep_locks.setdefault(dep_id, asyncio.Lock())
    before, _ = db.effective_variables(req.get("folder_id"))
    waiting_since = time.time()
    async with lock:
        last = db.dependency_ran(dep_id)
        age = (time.time() - last["ran_at"]) if last else None
        if not force and last and last["ok"] and last["ran_at"] >= waiting_since:
            # it was fetched while this send waited for the lock (parallel bodies): use that result,
            # even with "send every time" (TTL 0), instead of fetching it again once per body in a row
            info = {"name": dep_req["name"], "id": dep_id, "state": "shared", "age": round(age or 0), "ttl": ttl}
        elif not force and last and last["ok"] and ttl > 0 and age is not None and age < ttl:
            info = {"name": dep_req["name"], "id": dep_id, "state": "fresh", "age": round(age), "ttl": ttl}
        else:
            item = expand(dep_req)[0]
            variables, env_name = db.effective_variables(dep_req.get("folder_id"))
            dctx = build_context(variables)
            dctx["_dep_chain"] = chain
            res = await execute(item, dctx)   # the dependency may itself depend on another request
            record(res, source=f"Auto: before {req.get('name') or 'request'}", req=item, environment=env_name)
            ok = res["status"] == "ok" and (res.get("status_code") or 0) < 400 and not res.get("tests_failed")
            db.set_dependency_ran(dep_id, ok, res.get("status_code"))
            info = {"name": dep_req["name"], "id": dep_id, "state": "ran", "ok": ok, "status_code": res.get("status_code"),
                    "duration_ms": res.get("duration_ms"), "age": 0, "ttl": ttl, "error": res.get("error")}
            if not ok:
                why = res.get("error") or (f"HTTP {res.get('status_code')}" if res.get("status_code") else "failed")
                if res.get("tests_failed"):
                    why = f"{res['tests_failed']} test(s) failed"
                err = _error_result(f"Dependency \u201c{dep_req['name']}\u201d failed: {why}. This request was not sent.",
                                    {"method": req.get("method"), "url": req.get("url"), "params": [], "headers": [], "body": None}, 0.0, req, [])
                return info, err
    # the dependency may have changed variables (token, ids): give the main request the new values
    after, _ = db.effective_variables(req.get("folder_id"))
    for key, value in after.items():
        if before.get(key) != value:
            ctx.setdefault("vars", {})[key] = value
    return info, None


async def execute(req: dict[str, Any], ctx: dict[str, Any], on_chunk: Any = None) -> dict[str, Any]:
    """Send one request, after the request it depends on when it has one. Never raises.
    With on_chunk (an async callable), the response streams: the status/headers and every body
    chunk are passed to it as they arrive (Server-Sent Events, chunked responses)."""
    dep = _dependency_of(req)
    if not dep:
        return await _execute_once(req, ctx, on_chunk)
    info, err = await _ensure_dependency(req, dep, ctx)
    if err:
        err["dependency"] = info
        return err
    result = await _execute_once(req, ctx, on_chunk)
    if on_chunk is None and dep.get("retry_on_auth", True) and result.get("status_code") in (401, 403) and info.get("state") == "fresh":
        # the cached token was rejected: refresh it once and send again
        info2, err2 = await _ensure_dependency(req, dep, ctx, force=True)
        if not err2:
            result = await _execute_once(req, ctx)
            info = {**info2, "retried": True}
        else:
            info = {**info2, "retried": False}
    result["dependency"] = info
    return result


_ssl_cache: dict[tuple[Any, ...], Any] = {}


def _ssl_context(verify: bool, cert: str | None = None, key: str | None = None) -> Any:
    """A shared SSL context. Building one loads the whole CA bundle (~100 ms of blocking work), which
    used to happen for every request and made parallel sends start one after another. Contexts with
    a client certificate are rebuilt when the certificate or key file changes."""
    stamp = tuple(Path(f).stat().st_mtime if f and Path(f).exists() else None for f in (cert, key))
    k = (verify, cert, key, stamp)
    ctx = _ssl_cache.get(k)
    if ctx is None:
        ctx = httpx.create_ssl_context(verify=verify)
        if cert:
            ctx.load_cert_chain(cert, key)
        if len(_ssl_cache) > 32:
            _ssl_cache.clear()
        _ssl_cache[k] = ctx
    return ctx


async def _execute_once(req: dict[str, Any], ctx: dict[str, Any], on_chunk: Any = None) -> dict[str, Any]:
    """Send one request. Never raises; network errors are reported in the result."""
    settings = db.app_settings()
    # which named body is being sent: {{body_name}} in the request, pm/py.variables.get("body_name") in scripts
    ctx.setdefault("vars", {})
    ctx["vars"]["body_name"] = req.get("body_name") or ""
    ctx["vars"]["body_id"] = req.get("body_id") or ""
    pre = apply_pre_vars(req, ctx)
    script_reports: dict[str, Any] = {}
    extracted_by_script: list[dict[str, Any]] = []
    sc = (req.get("options") or {}).get("scripts") or {}
    # the script for all bodies runs first, then the one written for the body being sent
    for key, pre_code in (("pre", sc.get("pre") or ""), ("pre_body", scripts_for_body(req)["pre"])):
        if not pre_code.strip():
            continue
        if sc.get("lang") == "py":
            req, report, local_vars = scripts.run_pre_request(pre_code, req, ctx["vars"], db.set_variable)
            ctx["vars"].update(local_vars)
        else:
            report = jsengine.run(pre_code, _js_state(req, ctx, None), event="prerequest")
            if report["ok"] and report.get("request"):
                req = _apply_js_request(req, report["request"])
            extracted_by_script += _apply_js_vars(report, req, ctx)
        if key == "pre_body":
            report["body_name"] = req.get("body_name") or ""
        script_reports[key] = report
        if not report["ok"]:
            res = _error_result(f"Pre-request script failed: {report['error']}",
                                {"method": req.get("method"), "url": req.get("url"), "params": [], "headers": [], "body": None},
                                0.0, req, pre)
            res["scripts"] = script_reports
            return res
    req = {**req, "_script_extracted": extracted_by_script}
    try:
        p = prepare(req, ctx, settings)
    except Exception as exc:  # noqa: BLE001 - e.g. unreadable file
        res = _error_result(f"{type(exc).__name__}: {exc}", {"method": req.get("method"), "url": req.get("url"),
                                                            "params": [], "headers": [], "body": None}, 0.0, req, pre)
        res["scripts"] = script_reports
        return res
    resolved = {"method": p["method"], "url": p["url"], "params": p["params"], "headers": p["headers"],
                "body": p["body"], "body_kind": p["body_kind"], "unresolved": p["unresolved"],
                "auth_type": p["auth_type"], "auth_from": p["auth_from"],
                "files": public_request(p)["files"]}
    timeout = float(req.get("timeout") or settings.get("default_timeout") or 30)
    started = time.perf_counter()

    if not p["url"]:
        return _error_result("Request has no URL", resolved, 0.0, req, pre)

    host = urlsplit(p["url"]).hostname or ""
    verify: Any = _ssl_context(db.verify_ssl_enabled())
    for cert in settings.get("certs") or []:
        if cert.get("host") and cert.get("cert") and _host_matches(host, cert["host"]):
            try:
                verify = _ssl_context(db.verify_ssl_enabled(), cert["cert"], cert.get("key") or None)
            except Exception as exc:  # noqa: BLE001
                return _error_result(f"Client certificate error: {exc}", resolved, 0.0, req, pre)
            break
    proxy = (settings.get("proxy_url") or "").strip() or None
    if proxy and any(_host_matches(host, b) for b in (settings.get("proxy_bypass") or "").split(",")):
        proxy = None

    jar = _jar_from_db() if p["send_cookies"] else CookieJar()
    client_kwargs: dict[str, Any] = {
        "timeout": timeout, "verify": verify, "follow_redirects": p["follow_redirects"],
        "max_redirects": int(settings.get("max_redirects") or 10), "cookies": jar,
    }
    if proxy:
        client_kwargs["proxy"] = proxy
    # httpx replaces the URL's own query string with `params` (even an empty list): append them instead
    send_url = p["url"]
    if p["params"]:
        send_url += ("&" if "?" in send_url else "?") + str(httpx.QueryParams(p["params"]))
    send_kwargs: dict[str, Any] = {"headers": p["headers"]}
    if p["body_kind"] == "text":
        send_kwargs["content"] = (p["body"] or "").encode("utf-8")
    elif p["body_kind"] == "form":
        send_kwargs["data"] = _form_dict(p["form"])
    elif p["body_kind"] == "multipart":
        if p["files"]:
            send_kwargs["data"] = _form_dict(p["form"])
            send_kwargs["files"] = p["files"]
        else:  # httpx would fall back to urlencoded without files; build the multipart body by hand
            boundary = "----pyhttp" + re.sub(r"[^a-z0-9]", "", str(time.time()))
            send_kwargs["content"] = _multipart(p["form"], boundary)
            send_kwargs["headers"] = [h for h in p["headers"] if h[0].lower() != "content-type"] + \
                [("Content-Type", f"multipart/form-data; boundary={boundary}")]
    elif p["body_kind"] == "binary":
        if p["body_bytes"] is None:
            return _error_result("Binary body file not found", resolved, 0.0, req, pre)
        send_kwargs["content"] = p["body_bytes"]
    if p["digest"]:
        send_kwargs["auth"] = httpx.DigestAuth(*p["digest"])

    async def oauth_header(force: bool = False) -> bool:
        """Put a token in the Authorization header; True when it came from the cache."""
        was_cached = not force and oauth2.cached(p["oauth2"]) is not None
        tok = await oauth2.fetch(p["oauth2"], client_kwargs, force=force)
        prefix = (p["oauth2"].get("prefix") or "").strip()
        value = f"{prefix} {tok['access_token']}".strip()
        send_kwargs["headers"] = [(k, value if k.lower() == "authorization" else v) for k, v in send_kwargs["headers"]]
        return was_cached

    try:
        token_was_cached = await oauth_header() if p["oauth2"] else False
        async with httpx.AsyncClient(**client_kwargs) as client:
            if on_chunk is None:
                resp = await client.request(p["method"], send_url, **send_kwargs)
                if resp.status_code == 401 and token_was_cached and p["body_kind"] != "multipart":
                    # the token may have been revoked or expired early: get a new one and try once more
                    await resp.aclose()
                    await oauth_header(force=True)
                    resp = await client.request(p["method"], send_url, **send_kwargs)
                raw = await resp.aread()
            else:
                # stream: status and headers out first, then every chunk as it arrives
                breq = client.build_request(p["method"], send_url, **send_kwargs)
                resp = await client.send(breq, stream=True)
                await on_chunk({"type": "status", "status_code": resp.status_code,
                                "reason": resp.reason_phrase, "http_version": resp.http_version,
                                "headers": [(k, v) for k, v in resp.headers.items()],
                                "content_type": resp.headers.get("content-type", "")})
                parts: list[bytes] = []
                total = 0
                try:
                    async for part in resp.aiter_bytes():
                        parts.append(part)
                        total += len(part)
                        if total <= MAX_STORED_BODY:  # the UI shows at most this much live
                            await on_chunk({"type": "chunk",
                                            "text": part.decode(resp.encoding or "utf-8", errors="replace")})
                finally:
                    await resp.aclose()
                raw = b"".join(parts)
            if p["store_cookies"]:
                try:
                    if p["send_cookies"]:  # the jar held everything stored: it is the new truth
                        db.replace_cookies(jar_to_list(client.cookies.jar))
                    else:  # cookies were not loaded: only add what this response set
                        for c in jar_to_list(client.cookies.jar):
                            db.upsert_cookie(c)
                except Exception:  # noqa: BLE001 - cookies are best effort
                    pass
    except Exception as exc:  # noqa: BLE001 - every failure must land in the result
        return _error_result(f"{type(exc).__name__}: {exc}", resolved, (time.perf_counter() - started) * 1000, req, pre)

    duration_ms = (time.perf_counter() - started) * 1000
    resolved["url"] = str(resp.request.url)
    resolved["headers"] = [(k, v) for k, v in resp.request.headers.items()]
    if p["body_kind"] == "multipart":
        try:
            resolved["body"] = resp.request.read().decode("utf-8", errors="replace")[:MAX_STORED_BODY]
        except Exception:  # noqa: BLE001 - a streamed body cannot be shown; keep the field list
            pass
    content_type = resp.headers.get("content-type", "")
    ct_main = content_type.split(";")[0].strip().lower()
    is_binary = ct_main.startswith(("image/", "audio/", "video/")) or ct_main in ("application/pdf", "application/octet-stream", "application/zip")
    text = "" if is_binary else (resp.text if on_chunk is None else raw.decode(resp.encoding or "utf-8", errors="replace"))
    is_json = False
    parsed: Any = None
    if text:
        try:
            parsed = json.loads(text)
            is_json = True
        except ValueError:
            is_json = False
    truncated = len(text) > MAX_STORED_BODY
    set_cookies = httpx.Cookies()
    for r in [*resp.history, resp]:
        try:
            set_cookies.extract_cookies(r)
        except Exception:  # noqa: BLE001
            pass
    result: dict[str, Any] = {
        "status": "ok",
        "status_code": resp.status_code,
        "reason": resp.reason_phrase,
        "duration_ms": duration_ms,
        "error": None,
        "request": resolved,
        "response": {
            "headers": [(k, v) for k, v in resp.headers.items()],
            "content_type": content_type,
            "size": len(raw),
            "body_text": text[:MAX_STORED_BODY],
            "truncated": truncated,
            "is_json": is_json,
            "is_binary": is_binary,
            "body_base64": base64.b64encode(raw).decode() if is_binary and len(raw) <= MAX_INLINE_BINARY else None,
            "http_version": resp.http_version,
            "redirects": [str(h.url) for h in resp.history],
            "hops": [{"status_code": h.status_code, "url": str(h.url), "location": h.headers.get("location", "")} for h in resp.history],
            "cookies": jar_to_list(set_cookies.jar),
        },
        "pre_vars": pre,
        "scripts": script_reports,
        "_json": parsed,
        "_content": raw,
    }
    _finish(result, req, ctx)
    return result


def _form_dict(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    d: dict[str, list[str]] = {}
    for k, v in pairs:
        d.setdefault(k, []).append(v)
    return {k: (v[0] if len(v) == 1 else v) for k, v in d.items()}


def _multipart(form: list[tuple[str, str]], boundary: str) -> bytes:
    parts = []
    for k, v in form:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def _error_result(error: str, resolved: dict[str, Any], duration_ms: float, req: dict[str, Any],
                  pre: list[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "status": "error", "status_code": None, "reason": "", "duration_ms": duration_ms,
        "error": error, "request": resolved, "response": None, "pre_vars": pre, "scripts": {}, "_content": b"", "_json": None,
    }
    _finish(result, req)
    return result


def _js_state(req: dict[str, Any], ctx: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    """Everything pm.js needs: request, response, variables by scope, run info."""
    env = db.active_environment()
    env_vars = {r["key"].strip(): r.get("value", "") for r in (env["variables"] if env else [])
                if r.get("enabled", True) and r.get("key", "").strip()}
    coll = db.inherited(req.get("folder_id"))["variables"] if req.get("folder_id") else {}
    if result is not None:
        r = result.get("request") or {}
        request = {"method": r.get("method"), "url": r.get("url"), "headers": [list(h) for h in r.get("headers") or []],
                   "params": [list(p) for p in r.get("params") or []], "body": r.get("body") or "",
                   "body_type": req.get("body_type", "none")}
    else:
        request = {"method": req.get("method"), "url": req.get("url"),
                   "headers": [[h.get("key", ""), h.get("value", "")] for h in req.get("headers") or [] if h.get("enabled", True) and h.get("key")],
                   "params": [[p.get("key", ""), p.get("value", "")] for p in req.get("params") or [] if p.get("enabled", True) and p.get("key")],
                   "body": req.get("body") or "", "body_type": req.get("body_type", "none"),
                   "auth": (req.get("auth") or {}).get("type")}
    response = None
    if result is not None:
        resp = result.get("response")
        if resp:
            response = {"code": result.get("status_code"), "status": result.get("reason") or "",
                        "headers": [list(h) for h in resp.get("headers") or []], "body": resp.get("body_text") or "",
                        "time_ms": result.get("duration_ms"), "size": resp.get("size"), "cookies": resp.get("cookies") or []}
        else:
            response = {"code": None, "status": "", "headers": [], "body": result.get("error") or "",
                        "time_ms": result.get("duration_ms"), "size": 0, "cookies": [], "error": result.get("error")}
    local = dict(ctx.get("locals") or {})  # set by pre-request variables and scripts for this request only
    for key in ("body_name", "body_id"):   # which named body is being sent: pm.variables.get("body_name")
        if key in (ctx.get("vars") or {}):
            local.setdefault(key, ctx["vars"][key])
    return {
        "request": request, "response": response,
        "variables": {"env": env_vars, "globals": db.get_variables(), "collection": coll,
                      "data": {str(k): ("" if v is None else str(v)) for k, v in (req.get("data_row") or {}).items()},
                      "local": local},
        "environmentName": env["name"] if env else "",
        "info": {"requestName": req.get("name", ""), "requestId": req.get("id") or "",
                 "iteration": max(0, int(req.get("iteration", 1)) - 1), "iterationCount": int(req.get("iteration_count", 1))},
    }


def _apply_js_request(req: dict[str, Any], changed: dict[str, Any]) -> dict[str, Any]:
    out = dict(req)
    if changed.get("method"):
        out["method"] = str(changed["method"]).upper()
    if changed.get("url") is not None:
        out["url"] = str(changed["url"])
    text = lambda v: "" if v is None else str(v)  # noqa: E731 - a script may add a header with an undefined value
    out["headers"] = [{"key": text(k), "value": text(v), "enabled": True} for k, v in changed.get("headers") or []]
    out["params"] = [{"key": text(k), "value": text(v), "enabled": True} for k, v in changed.get("params") or []]
    if out.get("body_type", "none") != "none" or (changed.get("body") or "").strip():
        out["body"] = changed.get("body") or ""
        if out.get("body_type", "none") == "none" and out["body"].strip():
            out["body_type"] = "json" if out["body"].lstrip().startswith(("{", "[")) else "text"
    return out


def _apply_js_vars(report: dict[str, Any], req: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Persist environment / global / collection changes made by a script; local ones go into ctx."""
    shown: list[dict[str, Any]] = []
    scopes = {"env": "env", "globals": "global", "collection": "collection"}
    for js_scope, scope in scopes.items():
        for name, value in (report.get("vars") or {}).get(js_scope, {}).items():
            used = db.set_variable(name, value, scope, req.get("folder_id"))
            ctx["vars"][name] = value
            shown.append({"name": name, "scope": used, "value": value, "ok": True, "note": "set by script"})
        for name in (report.get("unset") or {}).get(js_scope, []):
            db.unset_variable(name, scope, req.get("folder_id"))
            ctx["vars"].pop(name, None)
            shown.append({"name": name, "scope": scope, "value": "", "ok": True, "note": "unset by script"})
    for name, value in (report.get("vars") or {}).get("local", {}).items():
        ctx["vars"][name] = value
        ctx.setdefault("locals", {})[name] = value
    return shown


def cancelled_result(req: dict[str, Any]) -> dict[str, Any]:
    """What a Send returns after the user pressed Cancel (tests are not evaluated)."""
    return {
        "status": "cancelled", "status_code": None, "reason": "", "duration_ms": 0.0, "error": "Cancelled by user",
        "request": {"method": (req.get("method") or "GET").upper(), "url": req.get("url", ""), "params": [], "headers": [],
                    "body": req.get("body") or None},
        "response": None, "pre_vars": [], "scripts": {}, "tests": [], "tests_failed": 0, "extracted": [],
        "_content": b"", "_json": None,
    }


def _finish(result: dict[str, Any], req: dict[str, Any], ctx: dict[str, Any] | None = None) -> None:
    """Tests and extractions run on every result, including errors (so "status is 200" fails visibly)."""
    tests = assertions.run_tests(tests_for_body(req), result)
    extracted = assertions.extract(req.get("extract") or [], result)
    for e in extracted:
        if e["ok"]:
            e["scope"] = db.set_variable(e["name"], e["value"] or "", e["scope"])
    extracted = list(req.get("_script_extracted") or []) + extracted
    sc = (req.get("options") or {}).get("scripts") or {}
    # the test script for all bodies, then the one written for the body that was sent
    for key, test_code in (("test", sc.get("test") or ""), ("test_body", scripts_for_body(req)["test"])):
        if not test_code.strip():
            continue
        if sc.get("lang") == "py":
            script_tests, report = scripts.run_tests(test_code, req, (ctx or {}).get("vars", {}), result, db.set_variable)
        else:
            report = jsengine.run(test_code, _js_state(req, ctx or {"vars": {}}, result), event="test")
            script_tests = list(report.get("tests") or [])
            extracted += _apply_js_vars(report, req, ctx or {"vars": {}})
            if not report["ok"]:
                script_tests.append({"name": "Test script", "passed": False, "note": report["error"] or "script error"})
        if key == "test_body":
            report["body_name"] = req.get("body_name") or ""
        tests.extend(script_tests)
        result.setdefault("scripts", {})[key] = report
    result["tests"] = tests
    result["tests_failed"] = sum(1 for t in tests if not t["passed"])
    result["extracted"] = extracted


def tests_for_body(req: dict[str, Any]) -> list[dict[str, Any]]:
    """The code-free tests that apply to the body being sent: those for all bodies plus those
    scoped to this body (a scope pointing at a body that no longer exists counts as "all")."""
    ids = {b.get("id") for b in req.get("bodies") or [] if b.get("id")}
    current = req.get("body_id") or ""
    return [t for t in req.get("tests") or [] if not t.get("body_id") or t["body_id"] not in ids or t["body_id"] == current]


def scripts_for_body(req: dict[str, Any]) -> dict[str, str]:
    """The pre-request and test scripts written for the body being sent (empty when there are none).
    They run after the scripts for all bodies; a script keyed by a body that no longer exists is ignored."""
    sc = (req.get("options") or {}).get("scripts") or {}
    ids = {b.get("id") for b in req.get("bodies") or [] if b.get("id")}
    current = req.get("body_id") or ""
    own = (sc.get("bodies") or {}).get(current) if current in ids else None
    own = own if isinstance(own, dict) else {}
    return {"pre": str(own.get("pre") or ""), "test": str(own.get("test") or "")}


def expand(req: dict[str, Any]) -> list[dict[str, Any]]:
    """One run item per enabled named body; a single item when the request sends no body."""
    if (req.get("body_type") or "none") == "none":
        return [{**req, "body": "", "body_name": None, "body_id": None}]
    enabled = [b for b in req.get("bodies") or [] if b.get("enabled", True)]
    if not enabled:
        return [{**req, "body": "", "body_name": None, "body_id": None}]
    if len(req["bodies"]) == 1 and enabled[0].get("name") == "Default":
        # a plain single-body request (e.g. migrated from before named bodies): no label
        return [{**req, "body": enabled[0].get("body", ""), "body_name": None, "body_id": enabled[0].get("id")}]
    return [{**req, "body": b.get("body", ""), "body_name": b.get("name"), "body_id": b.get("id")} for b in enabled]


def display_name(item: dict[str, Any]) -> str:
    return f"{item['name']} · {item['body_name']}" if item.get("body_name") else item["name"]


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def prev_context(result: dict[str, Any]) -> dict[str, Any]:
    resp = result.get("response") or {}
    return {
        "status": result.get("status_code"),
        "ok": result.get("status") == "ok",
        "error": result.get("error"),
        "body": resp.get("body_text", ""),
        "json": result.get("_json"),
        "headers": {k.lower(): v for k, v in resp.get("headers", [])},
        "duration_ms": result.get("duration_ms"),
    }


def snapshot(req: dict[str, Any]) -> dict[str, Any]:
    """What the history keeps so a past request can be reopened as a new request."""
    keys = ("name", "description", "tags", "method", "url", "params", "headers", "body_type", "auth", "timeout",
            "tests", "extract", "pre_vars", "options", "folder_id")
    snap = {k: req.get(k) for k in keys if k in req}
    snap["body"] = req.get("body") or ""
    snap["bodies"] = [{"id": "default", "name": req.get("body_name") or "Default", "body": req.get("body") or "", "enabled": True}] if req.get("body") else []
    return snap


SECRET_MASK = "••••••"


def mask_secrets(obj: Any, secrets: list[str]) -> Any:
    """A copy of obj with every secret value replaced by SECRET_MASK (str, list, tuple, dict)."""
    if not secrets:
        return obj
    if isinstance(obj, str):
        for s in secrets:
            if s in obj:
                obj = obj.replace(s, SECRET_MASK)
        return obj
    if isinstance(obj, dict):
        return {k: mask_secrets(v, secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(mask_secrets(v, secrets) for v in obj)
    return obj


# ---- full bodies of history entries
# The history keeps 64 KB of a response body and the viewer 512 KB. A body longer than what the
# history keeps is also written whole to a file named after the history entry, so a response view
# (and nothing else: the list stays light) gets the body exactly as the API sent it. The files
# live next to the run outputs and go away with their history entries.
BODY_DIR = "_bodies"
HISTORY_BODY_CHARS = 64_000
MAX_KEPT_BODY = 64 * 1024 * 1024   # larger bodies are not kept in full


def bodies_dir() -> Path:
    return db.current().path.parent / "outputs" / BODY_DIR


def full_body(history_id: str | None) -> bytes | None:
    """The whole response body of a history entry, when it was kept."""
    if not history_id or not re.fullmatch(r"[0-9a-f]{8,64}", str(history_id)):
        return None
    try:
        return (bodies_dir() / f"{history_id}.body").read_bytes()
    except OSError:
        return None


def sweep_bodies() -> int:
    """Delete the body files of history entries that no longer exist; returns how many."""
    folder = bodies_dir()
    if not folder.is_dir():
        return 0
    keep = db.history_ids()
    gone = 0
    for f in folder.glob("*.body"):
        if f.stem not in keep:
            try:
                f.unlink()
                gone += 1
            except OSError:
                pass
    return gone


_bodies_written = 0


def _keep_full_body(history_id: str, result: dict[str, Any]) -> None:
    global _bodies_written
    resp = result.get("response") or {}
    content = result.get("_content")
    cut = resp.get("truncated") or resp.get("is_binary") or len(resp.get("body_text") or "") > HISTORY_BODY_CHARS
    if not cut or not isinstance(content, (bytes, bytearray)) or not content or len(content) > MAX_KEPT_BODY:
        return
    folder = bodies_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{history_id}.body").write_bytes(content)
    _bodies_written += 1
    if _bodies_written % 25 == 1:   # the history is capped: drop the files of entries it pushed out
        sweep_bodies()


def record(result: dict[str, Any], *, source: str, req: dict[str, Any], environment: str | None) -> str | None:
    """Console (in memory) and history (persisted) entry for an executed request. Returns the id
    of the history entry (None when it could not be written), so the UI can compare two sends."""
    console.record(result, source=source, request_name=req.get("name", ""), body_name=req.get("body_name"),
                   environment=environment)
    resp = result.get("response")
    detail = {"request": result.get("request"), "response": None, "reason": result.get("reason"),
              "tests": result.get("tests"), "extracted": result.get("extracted")}
    if resp:
        detail["response"] = {**resp, "body_text": (resp.get("body_text") or "")[:64_000],
                              "body_base64": None, "truncated": resp.get("truncated") or len(resp.get("body_text") or "") > 64_000}
    try:
        secrets = db.secret_values(req.get("folder_id"))
        detail = mask_secrets(detail, secrets)
        history_id = db.add_history({
            "ts": time.time(), "source": source, "method": (result.get("request") or {}).get("method", ""),
            "url": mask_secrets((result.get("request") or {}).get("url", ""), secrets), "request_id": req.get("id"),
            "request_name": req.get("name", ""), "body_name": req.get("body_name"), "status": result.get("status", ""),
            "status_code": result.get("status_code"), "duration_ms": result.get("duration_ms"),
            "error": result.get("error"), "environment": environment, "snapshot": snapshot(req), "detail": detail,
        })
        _keep_full_body(history_id, result)
        return history_id
    except Exception:  # noqa: BLE001 - history must never break a send
        return None


class RunManager:
    """Owns background tasks for sequential runs."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def is_active(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return t is not None and not t.done()

    def any_active(self) -> bool:
        return any(not t.done() for t in self._tasks.values())

    async def start_saved(self, request_ids: list[str], options: dict[str, Any]) -> dict[str, Any]:
        requests = db.get_requests(request_ids)
        if not requests:
            raise ValueError("No valid requests selected")
        items = [item for r in requests for item in expand(r)]
        data_rows = [r for r in (options.get("data") or []) if isinstance(r, dict)]
        iterations = max(1, int(options.get("iterations") or 1))
        if data_rows:
            iterations = len(data_rows)
        runs: list[dict[str, Any]] = []
        for i in range(1, iterations + 1):
            row = data_rows[i - 1] if data_rows else {}
            runs.extend({**it, "iteration": i, "iteration_count": iterations, "seq": j, "data_row": row}
                        for j, it in enumerate(items, start=1))
        options = {**options, "iterations": iterations, "data_rows": len(data_rows)}
        options.pop("data", None)
        return await self.start(runs, options)

    async def start(self, requests: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
        """Start a run over already-expanded items (each item is a request with one body)."""
        if not requests:
            raise ValueError("Nothing to send")
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = (options.get("name") or "").strip() or f"Run {stamp}"
        out_dir = self.output_root / f"{stamp}_{_slug(name)}"
        n = 1
        while out_dir.exists():
            n += 1
            out_dir = self.output_root / f"{stamp}_{_slug(name)}_{n}"
        out_dir.mkdir(parents=True, exist_ok=True)
        options = {**options, "environment": db.effective_variables()[1]}
        run = db.create_run(name, len(requests), options, str(out_dir))
        task = asyncio.create_task(self._execute(run["id"], requests, options, out_dir))
        self._tasks[run["id"]] = task
        return run

    def cancel(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    async def _execute(
        self, run_id: str, requests: list[dict[str, Any]], options: dict[str, Any], out_dir: Path
    ) -> None:
        env_name = db.effective_variables()[1]
        source = f"Send ×{len(requests)}" if options.get("mode") == "bodies" else f"Run: {options.get('name') or 'run'}"
        stop_on_error = bool(options.get("stop_on_error", False))
        fail_on_http_error = bool(options.get("fail_on_http_error", True))
        fail_on_test = bool(options.get("fail_on_test", True))
        delay_ms = max(0.0, float(options.get("delay_ms") or 0))
        multi_iter = int(options.get("iterations") or 1) > 1
        parallel = bool(options.get("parallel"))
        concurrency = max(1, min(64, int(options.get("concurrency") or 8)))
        prev: dict[str, Any] | None = None
        completed = failed = 0
        status = "completed"
        stopped = False
        summary: dict[str, Any] = {
            "run_id": run_id, "name": options.get("name"), "started_at": datetime.now().isoformat(),
            "options": options, "results": [],
        }
        async def send_one(pos: int, req: dict[str, Any], prev: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
            """Send one item, write its files and record it. Returns (failed, context for {{prev.*}})."""
            nonlocal completed, failed
            started_at = time.time()
            variables, _ = db.effective_variables(req.get("folder_id"))  # re-read: extractions may have changed them
            ctx = build_context(variables, prev, req.get("data_row"))
            if req.get("data_row"):
                ctx["vars"].update({str(k): ("" if v is None else str(v)) for k, v in req["data_row"].items()})
            result = await execute(req, ctx)
            history_id = record(result, source=source, req=req, environment=env_name)
            failed_req = result["status"] == "error" or (
                fail_on_http_error and (result.get("status_code") or 0) >= 400
            ) or (fail_on_test and result.get("tests_failed", 0) > 0)
            stem = f"{req.get('seq') or pos:02d}_{_slug(req['name'])}"
            if multi_iter:
                stem = f"i{req.get('iteration', 1):02d}_" + stem
            if req.get("body_name"):
                stem += f"__{_slug(req['body_name'])}"
            body_file = detail_file = None
            if result.get("response") is not None:
                body_file = stem + ".body" + _ext_for(result["response"]["content_type"])
                (out_dir / body_file).write_bytes(result["_content"])
            detail_file = stem + ".json"
            secrets = db.secret_values(req.get("folder_id"))
            public_resp = None
            if result.get("response"):
                public_resp = {k: v for k, v in result["response"].items() if k != "body_base64"}
            masked_req = mask_secrets(result["request"], secrets)
            masked_extracted = mask_secrets(result.get("extracted"), secrets)
            detail = {
                "request": {
                    "id": req.get("id"), "name": req["name"], "body_name": req.get("body_name"),
                    "description": req.get("description"), "iteration": req.get("iteration", 1),
                    "tags": req.get("tags"), "resolved": masked_req,
                },
                "status": "failed" if failed_req else "ok",
                "status_code": result["status_code"],
                "reason": result["reason"],
                "duration_ms": round(result["duration_ms"], 2),
                "error": result["error"],
                "tests": result.get("tests"),
                "extracted": masked_extracted,
                "dependency": result.get("dependency"),
                "response": public_resp,
                "body_file": body_file,
                "started_at": datetime.fromtimestamp(started_at).isoformat(),
            }
            (out_dir / detail_file).write_text(
                json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            db.add_result(run_id, {
                "position": pos, "request_id": req.get("id"), "request_name": req["name"],
                "body_name": req.get("body_name"), "iteration": req.get("iteration", 1),
                "method": result["request"]["method"],
                "status": "failed" if failed_req else "ok",
                "status_code": result["status_code"], "duration_ms": result["duration_ms"],
                "error": result["error"], "tests": result.get("tests"),
                "detail": {"request": masked_req, "response": public_resp,
                           "reason": result["reason"], "extracted": masked_extracted,
                           "dependency": result.get("dependency"), "history_id": history_id},
                "body_file": body_file, "detail_file": detail_file, "started_at": started_at,
            })
            completed += 1
            failed += int(failed_req)
            summary["results"].append({
                "position": pos, "name": req["name"], "body_name": req.get("body_name"),
                "iteration": req.get("iteration", 1),
                "method": result["request"]["method"],
                "url": masked_req["url"], "status": detail["status"],
                "status_code": result["status_code"], "duration_ms": detail["duration_ms"],
                "error": result["error"], "detail_file": detail_file, "body_file": body_file,
                "tests": result.get("tests"),
            })
            db.update_run(run_id, completed=completed, failed=failed)
            self._write_summary(out_dir, summary, "running")
            return failed_req, prev_context(result)

        try:
            if parallel:
                # every item at once (bounded by `concurrency`); no {{prev.*}} since there is no previous response
                sem = asyncio.Semaphore(concurrency)

                async def bounded(pos: int, req: dict[str, Any]) -> None:
                    nonlocal stopped
                    async with sem:
                        if stopped:
                            return
                        failed_req, _ = await send_one(pos, req, None)
                        if failed_req and stop_on_error:
                            stopped = True

                await asyncio.gather(*(bounded(pos, req) for pos, req in enumerate(requests, start=1)))
                if stopped:
                    status = "stopped"
            else:
                for pos, req in enumerate(requests, start=1):
                    failed_req, prev = await send_one(pos, req, prev)
                    if failed_req and stop_on_error:
                        status = "stopped"
                        break
                    if delay_ms and pos < len(requests):
                        await asyncio.sleep(delay_ms / 1000)
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:  # noqa: BLE001
            status = "error"
            summary["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            summary["results"].sort(key=lambda r: r["position"])   # parallel sends finish in any order
            summary["finished_at"] = datetime.now().isoformat()
            summary["completed"] = completed
            summary["failed"] = failed
            self._write_summary(out_dir, summary, status)
            db.update_run(run_id, status=status, completed=completed, failed=failed,
                          finished_at=time.time())
            self._tasks.pop(run_id, None)

    @staticmethod
    def _write_summary(out_dir: Path, summary: dict[str, Any], status: str) -> None:
        summary["status"] = status
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
