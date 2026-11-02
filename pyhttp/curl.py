"""Turn a `curl ...` command line into a pyhttp request dict (Postman's "Import > Raw text")."""
from __future__ import annotations

import base64
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_DATA_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json"}


def _split(command: str) -> list[str]:
    text = command.strip()
    # line continuations from copied shell / PowerShell / cmd snippets
    for cont in ("\\\n", "`\n", "^\n", "\\\r\n", "`\r\n", "^\r\n"):
        text = text.replace(cont, " ")
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return shlex.split(text.replace("'", '"'), posix=True)


def parse(command: str) -> dict[str, Any]:
    """Parse the common curl flags. Unknown flags are ignored. Raises ValueError without a URL."""
    args = _split(command)
    if args and args[0].lower() in ("curl", "curl.exe"):
        args = args[1:]
    method: str | None = None
    url = ""
    headers: list[dict[str, Any]] = []
    data_parts: list[str] = []
    form_parts: list[str] = []
    urlencode_parts: list[str] = []
    user: str | None = None
    is_json_flag = False
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None

        def take() -> str:
            nonlocal i
            i += 1
            return args[i] if i < len(args) else ""

        if a in ("-X", "--request"):
            method = take().upper()
        elif a.startswith("-X") and len(a) > 2:
            method = a[2:].upper()
        elif a in ("-H", "--header"):
            h = take()
            k, _, v = h.partition(":")
            if k.strip():
                headers.append({"key": k.strip(), "value": v.strip(), "enabled": True})
        elif a in _DATA_FLAGS:
            v = take()
            if a == "--data-urlencode":
                urlencode_parts.append(v)
            else:
                data_parts.append(v[1:] if v.startswith("@") and a == "--data-binary" else v)
            if a == "--json":
                is_json_flag = True
        elif a.startswith("-d") and len(a) > 2:
            data_parts.append(a[2:])
        elif a in ("-F", "--form", "--form-string"):
            form_parts.append(take())
        elif a in ("-u", "--user"):
            user = take()
        elif a in ("-A", "--user-agent"):
            headers.append({"key": "User-Agent", "value": take(), "enabled": True})
        elif a in ("-e", "--referer"):
            headers.append({"key": "Referer", "value": take(), "enabled": True})
        elif a in ("-b", "--cookie"):
            headers.append({"key": "Cookie", "value": take(), "enabled": True})
        elif a == "--url":
            url = take()
        elif a in ("-k", "--insecure", "-L", "--location", "-s", "--silent", "-S", "--show-error", "-v",
                   "--verbose", "-i", "--include", "--compressed", "-g", "--globoff", "-#", "--http1.1",
                   "--http2", "-f", "--fail"):
            pass
        elif a in ("-o", "--output", "-m", "--max-time", "--connect-timeout", "-x", "--proxy", "--cacert",
                   "-E", "--cert", "--key", "-w", "--write-out", "--retry", "-c", "--cookie-jar", "-T", "--upload-file"):
            take()
        elif a.startswith("-") and nxt is not None and not nxt.startswith("-") and a not in ("-G", "--get"):
            # unknown flag with a value: skip both
            i += 1
        elif not a.startswith("-") and not url:
            url = a
        i += 1
    if not url:
        raise ValueError("No URL found in the curl command")
    if "://" not in url:
        url = "http://" + url

    auth: dict[str, Any] = {"type": "inherit"}
    if user:
        u, _, pw = user.partition(":")
        auth = {"type": "basic", "username": u, "password": pw}
    else:
        for h in headers:
            if h["key"].lower() == "authorization":
                v = h["value"]
                if v.lower().startswith("bearer "):
                    auth = {"type": "bearer", "token": v[7:].strip(), "prefix": "Bearer"}
                    headers.remove(h)
                elif v.lower().startswith("basic "):
                    try:
                        u, _, pw = base64.b64decode(v[6:].strip()).decode().partition(":")
                        auth = {"type": "basic", "username": u, "password": pw}
                        headers.remove(h)
                    except Exception:  # noqa: BLE001 - keep the raw header
                        pass
                break

    body_type = "none"
    body = ""
    ctype = next((h["value"].lower() for h in headers if h["key"].lower() == "content-type"), "")
    if form_parts:
        body_type = "multipart"
        lines = []
        for part in form_parts:
            k, _, v = part.partition("=")
            lines.append(f"{k}: {v}" if v.startswith("@") else f"{k}: {v}")
        body = "\n".join(lines)
    elif data_parts or urlencode_parts:
        raw = "&".join(data_parts + urlencode_parts)
        if is_json_flag or "json" in ctype or raw.lstrip().startswith(("{", "[")):
            body_type = "json"
            body = raw
            if is_json_flag and not ctype:
                headers.append({"key": "Content-Type", "value": "application/json", "enabled": True})
        elif "xml" in ctype:
            body_type, body = "xml", raw
        elif ctype and not ctype.startswith("application/x-www-form-urlencoded"):
            body_type, body = "text", raw
        else:
            body_type = "form"
            body = "\n".join(f"{k}: {v}" for k, v in parse_qsl(raw, keep_blank_values=True)) or raw
    if method is None:
        method = "POST" if body_type != "none" else "GET"

    parts = urlsplit(url)
    params = [{"key": k, "value": v, "enabled": True} for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment)) if params else url
    name = parts.path.rstrip("/").split("/")[-1] or parts.netloc
    return {
        "name": f"{method} {name}"[:80], "method": method, "url": clean_url, "params": params, "headers": headers,
        "body_type": body_type, "body": body,
        "bodies": [{"id": "default", "name": "Default", "body": body, "enabled": True}] if body else [],
        "auth": auth, "tags": [], "description": f"Imported from cURL:\n{command.strip()[:2000]}",
    }
