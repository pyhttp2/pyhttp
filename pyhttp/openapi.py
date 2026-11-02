"""Import OpenAPI 3.x / Swagger 2.0 specs and HAR files as collections.

Both produce the same import shape as postman.from_collection():
{name, folders, requests, variables, environments}.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlsplit

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def parse_text(text: str) -> Any:
    """JSON, or YAML when PyYAML is installed (OpenAPI files are often YAML)."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        raise ValueError("not valid JSON (YAML needs the PyYAML package)") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"not valid JSON or YAML: {exc}") from None


# ------------------------------------------------------------------ OpenAPI


def is_openapi(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("paths"), dict) and (
        str(data.get("openapi", "")).startswith("3") or str(data.get("swagger", "")).startswith("2"))


class _Spec:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def deref(self, obj: Any, seen: frozenset[str] = frozenset()) -> Any:
        """Follow a local $ref (#/components/...); unknown or circular refs become {}."""
        while isinstance(obj, dict) and isinstance(obj.get("$ref"), str):
            ref = obj["$ref"]
            if ref in seen or not ref.startswith("#/"):
                return {}
            seen = seen | {ref}
            node: Any = self.data
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                node = node.get(part) if isinstance(node, dict) else None
            obj = node if node is not None else {}
        return obj

    def example(self, schema: Any, depth: int = 0, seen: frozenset[str] = frozenset()) -> Any:
        """A sample value for a JSON schema: example, default, enum[0], or a typed placeholder."""
        if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
            ref = schema["$ref"]
            if ref in seen:
                return {}
            return self.example(self.deref(schema), depth, seen | {ref})
        if not isinstance(schema, dict) or depth > 8:
            return None
        for key in ("example", "default"):
            if key in schema:
                return schema[key]
        if isinstance(schema.get("examples"), list) and schema["examples"]:
            return schema["examples"][0]
        if schema.get("enum"):
            return schema["enum"][0]
        for key in ("allOf",):
            if isinstance(schema.get(key), list):
                merged: dict[str, Any] = {}
                for part in schema[key]:
                    v = self.example(part, depth + 1, seen)
                    if isinstance(v, dict):
                        merged.update(v)
                return merged
        for key in ("oneOf", "anyOf"):
            if isinstance(schema.get(key), list) and schema[key]:
                return self.example(schema[key][0], depth + 1, seen)
        t = schema.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), None)
        if t == "object" or (t is None and "properties" in schema):
            props = schema.get("properties") or {}
            return {k: self.example(v, depth + 1, seen) for k, v in props.items()}
        if t == "array":
            item = self.example(schema.get("items") or {}, depth + 1, seen)
            return [item] if item is not None else []
        if t == "integer":
            return 0
        if t == "number":
            return 0.0
        if t == "boolean":
            return True
        if t == "string":
            fmt = schema.get("format", "")
            return {"date-time": "2024-01-01T00:00:00Z", "date": "2024-01-01", "email": "user@example.com",
                    "uuid": "00000000-0000-0000-0000-000000000000", "uri": "https://example.com",
                    "binary": ""}.get(fmt, "string")
        return None


def _var_name(name: str) -> str:
    """Path variables are `:name` with word characters only."""
    v = re.sub(r"\W", "_", name)
    return v if re.match(r"[A-Za-z_]", v) else "_" + v


def _base_url(data: dict[str, Any]) -> str:
    if data.get("swagger"):
        host = data.get("host") or "localhost"
        scheme = (data.get("schemes") or ["https"])[0]
        return f"{scheme}://{host}{data.get('basePath') or ''}".rstrip("/")
    servers = data.get("servers") or []
    if not servers or not isinstance(servers[0], dict):
        return "http://localhost"
    url = str(servers[0].get("url") or "")
    for name, var in (servers[0].get("variables") or {}).items():
        url = url.replace("{" + name + "}", str((var or {}).get("default", "")))
    return url.rstrip("/") or "http://localhost"


def _security_auth(spec: _Spec, requirements: Any) -> dict[str, Any] | None:
    """Map the first usable security requirement to a pyhttp auth (values left as variables)."""
    data = spec.data
    schemes = (data.get("components") or {}).get("securitySchemes") or data.get("securityDefinitions") or {}
    if requirements == []:
        return {"type": "none"}  # security: [] turns authentication off
    for req in requirements or []:
        if not isinstance(req, dict):
            continue
        if not req:
            return {"type": "none"}  # {} in the list: security is optional
        for name in req:
            s = spec.deref(schemes.get(name) or {})
            t, scheme = s.get("type"), str(s.get("scheme", "")).lower()
            if (t == "http" and scheme == "basic") or t == "basic":
                return {"type": "basic", "username": "{{username}}", "password": "{{password}}"}
            if t == "http" and scheme == "bearer":
                return {"type": "bearer", "token": "{{token}}", "prefix": "Bearer"}
            if t == "apiKey" and s.get("in") in ("header", "query"):
                return {"type": "apikey", "key": s.get("name") or "X-API-Key", "value": "{{apiKey}}",
                        "add_to": "query" if s.get("in") == "query" else "header"}
            if t == "oauth2":
                flows = s.get("flows") or {}
                if "clientCredentials" in flows or s.get("flow") == "application":
                    f = flows.get("clientCredentials") or s
                    return {"type": "oauth2", "grant": "client_credentials", "token_url": f.get("tokenUrl", ""),
                            "client_id": "{{clientId}}", "client_secret": "{{clientSecret}}",
                            "scope": " ".join((f.get("scopes") or {}).keys())}
                if "password" in flows or s.get("flow") == "password":
                    f = flows.get("password") or s
                    return {"type": "oauth2", "grant": "password", "token_url": f.get("tokenUrl", ""),
                            "client_id": "{{clientId}}", "client_secret": "{{clientSecret}}",
                            "username": "{{username}}", "password": "{{password}}",
                            "scope": " ".join((f.get("scopes") or {}).keys())}
                return {"type": "bearer", "token": "{{token}}", "prefix": "Bearer"}
    return None


def _param_value(spec: _Spec, p: dict[str, Any]) -> str:
    if "example" in p:
        v = p["example"]
    else:
        schema = p.get("schema") or {k: p[k] for k in ("type", "default", "enum", "format", "items") if k in p}
        v = spec.example(schema)
    if v is None or isinstance(v, (dict, list)):
        return "" if v is None else json.dumps(v)
    return str(v).lower() if isinstance(v, bool) else str(v)


def _body_from_content(spec: _Spec, content: dict[str, Any]) -> tuple[str, str, str]:
    """(body_type, body text, content type) for an OpenAPI 3 requestBody content map."""
    order = sorted(content, key=lambda ct: (0 if "json" in ct else 1 if "x-www-form-urlencoded" in ct
                                            else 2 if "multipart" in ct else 3 if "xml" in ct else 4))
    for ct in order:
        media = content[ct] or {}
        if "example" in media:
            sample = media["example"]
        elif isinstance(media.get("examples"), dict) and media["examples"]:
            sample = spec.deref(next(iter(media["examples"].values()))).get("value")
        else:
            sample = spec.example(media.get("schema") or {})
        if "json" in ct:
            return "json", json.dumps(sample if sample is not None else {}, indent=2, ensure_ascii=False), ct
        if "x-www-form-urlencoded" in ct or "multipart" in ct:
            rows = sample if isinstance(sample, dict) else {}
            lines = []
            props = (spec.deref(media.get("schema") or {}).get("properties") or {})
            for k, v in rows.items():
                is_file = (spec.deref(props.get(k) or {}).get("format") == "binary")
                lines.append(f"// {k}: @path/to/file" if is_file else f"{k}: {'' if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v)}")
            return ("form" if "urlencoded" in ct else "multipart"), "\n".join(lines), ct
        if "xml" in ct:
            return "xml", sample if isinstance(sample, str) else "", ct
        if ct.startswith("text/"):
            return "text", sample if isinstance(sample, str) else "", ct
    return "none", "", ""


def from_openapi(data: dict[str, Any]) -> dict[str, Any]:
    spec = _Spec(data)
    info = data.get("info") or {}
    root_name = str(info.get("title") or "Imported API")
    root_id = uuid.uuid4().hex
    root_auth = _security_auth(spec, data.get("security")) or {"type": "none"}
    folders: list[dict[str, Any]] = [{
        "id": root_id, "name": root_name, "parent_id": None,
        "description": "\n\n".join(x for x in (str(info.get("description") or ""),
                                                f"Version {info['version']}" if info.get("version") else "") if x),
        "auth": root_auth, "headers": [],
        "variables": [{"key": "baseUrl", "value": _base_url(data), "enabled": True}],
    }]
    tag_folders: dict[str, str] = {}
    requests: list[dict[str, Any]] = []

    def folder_for(tag: str | None) -> str:
        if not tag:
            return root_id
        if tag not in tag_folders:
            tag_folders[tag] = uuid.uuid4().hex
            desc = next((str(t.get("description") or "") for t in data.get("tags") or [] if isinstance(t, dict) and t.get("name") == tag), "")
            folders.append({"id": tag_folders[tag], "name": tag, "parent_id": root_id, "description": desc,
                            "auth": {"type": "inherit"}, "headers": [], "variables": []})
        return tag_folders[tag]

    for path, item in (data.get("paths") or {}).items():
        item = spec.deref(item)
        if not isinstance(item, dict):
            continue
        shared = [spec.deref(p) for p in item.get("parameters") or []]
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params_by_key = {(p.get("in"), p.get("name")): p for p in shared if isinstance(p, dict)}
            for p in op.get("parameters") or []:
                p = spec.deref(p)
                if isinstance(p, dict):
                    params_by_key[(p.get("in"), p.get("name"))] = p
            url_path = re.sub(r"\{([^}/]+)\}", lambda m: ":" + _var_name(m.group(1)), path)
            query, headers, path_rows, form_rows = [], [], [], []
            body_type, body, body_ct = "none", "", ""
            for (where, name), p in params_by_key.items():
                if not name:
                    continue
                row = {"key": name, "value": _param_value(spec, p), "enabled": bool(p.get("required")) or where == "path"}
                if where == "query":
                    query.append(row)
                elif where == "header" and name.lower() not in ("authorization", "content-type", "accept"):
                    headers.append(row)
                elif where == "path":
                    path_rows.append({**row, "key": _var_name(name)})
                elif where == "formData":  # Swagger 2
                    form_rows.append(f"// {name}: @path/to/file" if p.get("type") == "file" else f"{name}: {row['value']}")
                elif where == "body":  # Swagger 2
                    sample = spec.example(p.get("schema") or {})
                    body_type, body = "json", json.dumps(sample if sample is not None else {}, indent=2, ensure_ascii=False)
            if form_rows and body_type == "none":
                consumes = op.get("consumes") or data.get("consumes") or []
                body_type = "multipart" if any("multipart" in c for c in consumes) else "form"
                body = "\n".join(form_rows)
            rb = spec.deref(op.get("requestBody") or {})
            if isinstance(rb.get("content"), dict):
                body_type, body, body_ct = _body_from_content(spec, rb["content"])
                if body_ct and body_type in ("json", "xml", "text") and body_ct not in ("application/json", "application/xml", "text/plain"):
                    headers.append({"key": "Content-Type", "value": body_ct, "enabled": True})
            auth = {"type": "inherit"}
            if "security" in op:
                auth = _security_auth(spec, op["security"]) or {"type": "inherit"}
            desc_parts = [str(op.get("description") or "")]
            if op.get("operationId"):
                desc_parts.append(f"operationId: `{op['operationId']}`")
            if op.get("deprecated"):
                desc_parts.insert(0, "**Deprecated.**")
            tags = op.get("tags") or []
            requests.append({
                "folder_id": folder_for(tags[0] if tags else None),
                "name": str(op.get("summary") or op.get("operationId") or f"{method.upper()} {path}")[:200],
                "description": "\n\n".join(x for x in desc_parts if x), "tags": [str(t) for t in tags[1:]],
                "method": method.upper(), "url": "{{baseUrl}}" + url_path,
                "params": path_rows + query, "headers": headers, "body_type": body_type, "body": body,
                "bodies": [{"id": "default", "name": "Default", "body": body, "enabled": True}] if body else [],
                "auth": auth, "tests": [], "timeout": 30,
            })
    return {"folders": folders, "requests": requests, "variables": {}, "environments": [], "name": root_name}


# ---------------------------------------------------------------------- HAR


def is_har(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("log"), dict) and isinstance(data["log"].get("entries"), list)


_SKIP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "cookie"}
_SKIP_TYPES = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".map", ".webp")


def from_har(data: dict[str, Any], include_assets: bool = False) -> dict[str, Any]:
    """Every request in a browser HAR export, one folder per host. Static assets (scripts,
    styles, images, fonts) are skipped unless include_assets is set."""
    entries = data["log"]["entries"]
    root_id = uuid.uuid4().hex
    root_name = "HAR import"
    pages = data["log"].get("pages") or []
    if pages and isinstance(pages[0], dict) and pages[0].get("title"):
        root_name = f"HAR: {str(pages[0]['title'])[:80]}"
    folders = [{"id": root_id, "name": root_name, "parent_id": None, "description": "Imported from a HAR file.",
                "auth": {"type": "none"}, "headers": [], "variables": []}]
    host_folders: dict[str, str] = {}
    requests: list[dict[str, Any]] = []
    for e in entries:
        r = (e or {}).get("request") or {}
        url = str(r.get("url") or "")
        if not url or url.startswith(("data:", "blob:", "chrome-extension:")):
            continue
        parts = urlsplit(url)
        if not include_assets and parts.path.lower().endswith(_SKIP_TYPES):
            continue
        host = parts.netloc or "local"
        if host not in host_folders:
            host_folders[host] = uuid.uuid4().hex
            folders.append({"id": host_folders[host], "name": host, "parent_id": root_id, "description": "",
                            "auth": {"type": "inherit"}, "headers": [], "variables": []})
        base = parts._replace(query="", fragment="").geturl()
        params = [{"key": k, "value": v, "enabled": True} for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        headers = [{"key": h.get("name", ""), "value": h.get("value", ""), "enabled": True}
                   for h in r.get("headers") or [] if isinstance(h, dict)
                   and h.get("name") and not h["name"].startswith(":") and h["name"].lower() not in _SKIP_HEADERS]
        body_type, body = "none", ""
        post = r.get("postData") or {}
        mime = str(post.get("mimeType") or "").lower()
        if post.get("params") and ("x-www-form-urlencoded" in mime or "multipart" in mime):
            body_type = "multipart" if "multipart" in mime else "form"
            body = "\n".join(f"{p.get('name', '')}: " + (f"@{p['fileName']}" if p.get("fileName") else str(p.get("value", "")))
                             for p in post["params"] if isinstance(p, dict))
            headers = [h for h in headers if h["key"].lower() != "content-type"]
        elif post.get("text") is not None:
            body = str(post["text"])
            body_type = "json" if "json" in mime else "xml" if "xml" in mime else "form" if "urlencoded" in mime else "text"
            if body_type == "json":
                try:
                    body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
                except ValueError:
                    pass
            elif body_type == "form":
                body = "\n".join(f"{k}: {v}" for k, v in parse_qsl(body, keep_blank_values=True))
        method = str(r.get("method") or "GET").upper()
        status = ((e or {}).get("response") or {}).get("status")
        requests.append({
            "folder_id": host_folders[host], "name": f"{method} {parts.path or '/'}"[:200],
            "description": f"Recorded response: HTTP {status}" if status else "", "tags": ["har"],
            "method": method, "url": base, "params": params, "headers": headers,
            "body_type": body_type, "body": body,
            "bodies": [{"id": "default", "name": "Default", "body": body, "enabled": True}] if body else [],
            "auth": {"type": "inherit"}, "tests": [], "timeout": 30,
        })
    return {"folders": folders, "requests": requests, "variables": {}, "environments": [], "name": root_name}
