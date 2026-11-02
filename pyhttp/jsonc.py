"""JSON with comments ("JSONC"), as used by the body editor.

The editor lets you comment out lines with `//` (Ctrl+/) or blocks with
`/* ... */`, like Postman. Comments are removed here before a JSON body is
sent. Everything inside strings is left untouched, so a URL such as
"http://x" inside a value is never mistaken for a comment.
"""
from __future__ import annotations


def strip_comments(text: str) -> str:
    """Remove `//` line comments and `/* */` block comments outside strings.

    Line breaks inside block comments are kept so line numbers stay stable.
    """
    if not text or ("//" not in text and "/*" not in text):
        return text or ""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j  # keep the newline itself
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = n if j == -1 else j + 2
            out.extend(ch for ch in text[i:end] if ch == "\n")
            i = end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def has_comments(text: str) -> bool:
    return strip_comments(text) != (text or "")
