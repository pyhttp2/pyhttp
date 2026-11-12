"""Collection documentation as Markdown (and the same as a standalone HTML page): folders,
requests, descriptions, example bodies, saved example responses, auth, tests. Secrets never
appear (header values that look like credentials are masked)."""
from __future__ import annotations

import html as _html
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
                out += "\n\n# variables\n" + (vars_ if isinstance(vars_, str) else json.dumps(vars_, indent=2))
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
        examples = (r.get("options") or {}).get("examples") or []
        if examples:
            out.append("**Example response**" if len(examples) == 1 else f"**Example responses** ({len(examples)})")
            out.append("")
            for e in examples:
                status = f"{e.get('status_code') or 200} {e.get('reason') or ''}".strip()
                line = f"*{e.get('name') or 'Example'}*: `{status}`"
                if e.get("content_type"):
                    line += f" · `{e['content_type']}`"
                out.append(line)
                out.append("")
                body = e.get("body") or ""
                if body.strip():
                    ct = (e.get("content_type") or "").lower()
                    kind = "json" if "json" in ct else "xml" if "xml" in ct else "html" if "html" in ct else "text"
                    text, lang = _pretty_body(body, kind)
                    if e.get("truncated"):
                        text = text.rstrip() + "\n… (cut: the example keeps the first part of a large body)"
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


# ------------------------------------------------------------------- HTML
# The same documentation as one self-contained HTML page (no scripts, no external files), in the
# colours of the app's theme. It is rendered from the Markdown above by a small converter that
# covers what to_markdown() emits and what people write in descriptions: headings, paragraphs,
# lists, tables, fenced code, `code`, **bold**, *italic* and [links](https://…).
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def _inline(text: str) -> str:
    out = []
    for i, part in enumerate(re.split(r"(`[^`\n]+`)", text)):
        if i % 2:   # a code span: nothing inside it is formatting
            code = part[1:-1]
            cls = f' class="m m-{code}"' if code in _METHODS else ""
            out.append(f"<code{cls}>{_html.escape(code, quote=False)}</code>")
            continue
        s = _html.escape(part, quote=True)
        s = re.sub(r"\[([^\]]+)\]\(((?:https?://|mailto:|#)[^)\s]*)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def markdown_to_html(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    stack: list[tuple[int, str]] = []   # open lists: (indent, tag)
    used: dict[str, int] = {}

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def close_lists(to_indent: int = -1) -> None:
        while stack and stack[-1][0] > to_indent:
            out.append(f"</li></{stack.pop()[1]}>")

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = re.match(r"^\s*(`{3,})\s*([\w+-]*)\s*$", line)
        if fence:
            flush_para()
            close_lists()
            body = []
            i += 1
            while i < len(lines) and not re.match(rf"^\s*{fence.group(1)}`*\s*$", lines[i]):
                body.append(lines[i])
                i += 1
            lang = f' class="lang-{fence.group(2)}"' if fence.group(2) else ""
            out.append(f"<pre><code{lang}>" + _html.escape("\n".join(body), quote=False) + "</code></pre>")
            i += 1
            continue
        head = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if head:
            flush_para()
            close_lists()
            level, text = len(head.group(1)), head.group(2)
            anchor = _anchor(text) or "section"
            used[anchor] = used.get(anchor, 0) + 1
            if used[anchor] > 1:
                anchor = f"{anchor}-{used[anchor]}"
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush_para()
            close_lists()
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in _cells(line)) + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(lines[i])) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        item = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if item:
            flush_para()
            indent, tag = len(item.group(1)), "ol" if item.group(2)[0].isdigit() else "ul"
            if stack and indent > stack[-1][0]:
                out.append(f"<{tag}><li>")
                stack.append((indent, tag))
            else:
                close_lists(indent)
                if stack and stack[-1][0] == indent:
                    out.append("</li><li>")
                else:
                    out.append(f"<{tag}><li>")
                    stack.append((indent, tag))
            out.append(_inline(item.group(3)))
            i += 1
            continue
        if not line.strip():
            flush_para()
            close_lists()
        else:
            if stack and not para and line.startswith(" "):   # a continuation line of the list item
                out.append(" " + _inline(line.strip()))
            else:
                close_lists()
                para.append(line.strip())
        i += 1
    flush_para()
    close_lists()
    return "\n".join(out)


_PALETTES = {
    "dark": {"bg": "#111418", "bg2": "#181c22", "bg3": "#1f242c", "border": "#2d3440", "text": "#e4e8ee", "muted": "#8b94a3",
             "primary": "#60a5fa", "get": "#4ade80", "post": "#fbbf24", "put": "#60a5fa", "patch": "#c084fc", "delete": "#f87171"},
    "light": {"bg": "#f6f7f9", "bg2": "#ffffff", "bg3": "#f0f2f5", "border": "#d6dbe3", "text": "#1c2330", "muted": "#5d6877",
              "primary": "#2563eb", "get": "#15803d", "post": "#b45309", "put": "#1d4ed8", "patch": "#7e22ce", "delete": "#dc2626"},
}


def _vars(name: str) -> str:
    return "".join(f"--{k}:{v};" for k, v in _PALETTES[name].items())


def to_html(title: str, markdown: str, theme: str = "dark") -> str:
    """A standalone page for ``markdown`` (from ``to_markdown``). ``theme`` is the app's setting:
    "dark", "light", or "system" (follows the reader's device)."""
    if theme == "system":
        root = f":root{{color-scheme:light dark;{_vars('light')}}}@media (prefers-color-scheme:dark){{:root{{{_vars('dark')}}}}}"
    else:
        theme = theme if theme in _PALETTES else "dark"
        root = f":root{{color-scheme:{theme};{_vars(theme)}}}"
    css = root + """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:28px;margin:0 0 4px}
h2{font-size:21px;margin:40px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--border)}
h3{font-size:17px;margin:30px 0 8px}
h4,h5,h6{font-size:15px;margin:24px 0 6px}
p{margin:8px 0}
a{color:var(--primary);text-decoration:none}
a:hover{text-decoration:underline}
code{font:13px ui-monospace,"Cascadia Code","JetBrains Mono",Menlo,Consolas,monospace;background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:1px 5px;overflow-wrap:anywhere}
pre{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 14px;overflow:auto;max-height:480px}
pre code{background:none;border:0;padding:0;overflow-wrap:normal}
table{border-collapse:collapse;margin:8px 0;width:100%}
th,td{text-align:left;padding:6px 10px;border:1px solid var(--border);vertical-align:top}
th{background:var(--bg3);font-weight:600}
ul,ol{padding-left:22px;margin:6px 0}
code.m{font-weight:700;background:none;border:0;padding:0 4px 0 0}
code.m-GET{color:var(--get)}code.m-POST{color:var(--post)}code.m-PUT{color:var(--put)}
code.m-PATCH{color:var(--patch)}code.m-DELETE{color:var(--delete)}code.m-HEAD,code.m-OPTIONS{color:var(--muted)}
footer{margin-top:48px;color:var(--muted);font-size:12px}
@media print{body{background:#fff;color:#000}pre{max-height:none}}
"""
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_html.escape(title)}</title><style>{css}</style></head>\n<body><main>\n"
            f"{markdown_to_html(markdown)}\n<footer>Generated by pyhttp.</footer>\n</main></body></html>\n")
