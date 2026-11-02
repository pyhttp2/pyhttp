"""OAuth 2.0 tokens for the "OAuth 2.0" auth type.

Grants that need no browser: client credentials and password (resource owner).
Tokens are cached in memory per (token URL, client, user, scope) and fetched
again when they expire; a refresh token is used first when the server gave one.
"""
from __future__ import annotations

import asyncio
import base64
import threading
import time
from typing import Any

import httpx

GRANTS = ("client_credentials", "password")
_EARLY = 30  # seconds: treat a token as expired this long before it really is

_cache: dict[tuple[str, ...], dict[str, Any]] = {}
_lock = threading.Lock()
_fetch_locks: dict[tuple[str, ...], asyncio.Lock] = {}


def _key(auth: dict[str, Any]) -> tuple[str, ...]:
    return (auth.get("grant") or "client_credentials", auth.get("token_url") or "", auth.get("client_id") or "",
            auth.get("username") or "", auth.get("scope") or "", auth.get("audience") or "")


def cached(auth: dict[str, Any]) -> dict[str, Any] | None:
    """The cached token for these (rendered) settings, if it is still valid."""
    with _lock:
        tok = _cache.get(_key(auth))
    if tok and (not tok.get("expires_at") or tok["expires_at"] - _EARLY > time.time()):
        return tok
    return None


def status(auth: dict[str, Any]) -> dict[str, Any]:
    """Token info for the UI (the token itself is shortened)."""
    with _lock:
        tok = _cache.get(_key(auth))
    if not tok:
        return {"has_token": False}
    exp = tok.get("expires_at")
    return {
        "has_token": True, "valid": not exp or exp - _EARLY > time.time(), "expires_at": exp,
        "token_type": tok.get("token_type") or "Bearer", "scope": tok.get("scope") or "",
        "preview": _preview(tok["access_token"]), "has_refresh": bool(tok.get("refresh_token")),
        "fetched_at": tok.get("fetched_at"),
    }


def _preview(token: str) -> str:
    return token if len(token) <= 16 else f"{token[:8]}…{token[-6:]}"


def forget(auth: dict[str, Any] | None = None) -> None:
    with _lock:
        if auth is None:
            _cache.clear()
        else:
            _cache.pop(_key(auth), None)


def _store(auth: dict[str, Any], data: dict[str, Any], old_refresh: str | None = None) -> dict[str, Any]:
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ValueError("token response has no access_token")
    try:
        expires_in = float(data.get("expires_in")) if data.get("expires_in") not in (None, "") else None
    except (TypeError, ValueError):
        expires_in = None
    now = time.time()
    tok = {
        "access_token": str(data["access_token"]),
        "token_type": str(data.get("token_type") or "Bearer"),
        "refresh_token": data.get("refresh_token") or old_refresh,
        "scope": str(data.get("scope") or auth.get("scope") or ""),
        "expires_at": now + expires_in if expires_in else None,
        "fetched_at": now,
    }
    with _lock:
        _cache[_key(auth)] = tok
    return tok


def _token_request(auth: dict[str, Any], refresh_token: str | None) -> tuple[dict[str, str], dict[str, str]]:
    """Form fields and headers of the token request."""
    if refresh_token:
        form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    elif auth.get("grant") == "password":
        form = {"grant_type": "password", "username": auth.get("username") or "", "password": auth.get("password") or ""}
    else:
        form = {"grant_type": "client_credentials"}
    if auth.get("scope"):
        form["scope"] = auth["scope"]
    if auth.get("audience"):
        form["audience"] = auth["audience"]
    headers = {"Accept": "application/json"}
    cid, secret = auth.get("client_id") or "", auth.get("client_secret") or ""
    if auth.get("client_auth") == "body":
        if cid:
            form["client_id"] = cid
        if secret:
            form["client_secret"] = secret
    elif cid or secret:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()
    return form, headers


async def fetch(auth: dict[str, Any], client_kwargs: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
    """A valid token: cached, refreshed, or newly requested. Raises ValueError with a readable message."""
    if not force:
        tok = cached(auth)
        if tok:
            return tok
    if not (auth.get("token_url") or "").strip():
        raise ValueError("OAuth 2.0: no token URL")
    key = _key(auth)
    lock = _fetch_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if not force:
            tok = cached(auth)  # another request may have fetched it while we waited
            if tok:
                return tok
        with _lock:
            old = _cache.get(key)
        refresh = old.get("refresh_token") if old else None
        kwargs = {k: v for k, v in (client_kwargs or {}).items() if k in ("verify", "proxy", "timeout")}
        kwargs.setdefault("timeout", 30)
        async with httpx.AsyncClient(**kwargs) as client:
            if refresh:
                form, headers = _token_request(auth, refresh)
                resp = await client.post(auth["token_url"], data=form, headers=headers)
                if resp.status_code < 400:
                    try:
                        return _store(auth, resp.json(), refresh)
                    except ValueError:
                        pass
            form, headers = _token_request(auth, None)
            resp = await client.post(auth["token_url"], data=form, headers=headers)
        if resp.status_code >= 400:
            detail = resp.text.strip()[:300]
            raise ValueError(f"OAuth 2.0 token request failed: HTTP {resp.status_code}" + (f" — {detail}" if detail else ""))
        try:
            data = resp.json()
        except ValueError:
            # some servers answer with a form-encoded body
            data = dict(httpx.QueryParams(resp.text))
        try:
            return _store(auth, data)
        except ValueError as exc:
            raise ValueError(f"OAuth 2.0: {exc}") from None
