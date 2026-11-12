"""A small WebSocket client (RFC 6455) on asyncio streams, for the WebSocket console of the app.

No dependency: the handshake is a plain HTTP/1.1 upgrade and the framing is a few bytes. What it
does: ws:// and wss://, text and binary messages, fragmented messages, ping/pong (pings are
answered), the closing handshake, subprotocols and extra request headers. What it does not:
compression (permessage-deflate is not offered, so servers send plain frames) and proxies.

A session keeps a log of events that the UI reads with ``events(after)``:

    {"n": 3, "ts": 1700000000.0, "dir": "in" | "out" | "info" | "error", "kind": "text" | "binary" |
     "ping" | "pong" | "open" | "close", "text": "...", "size": 12}
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import struct
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_EVENTS = 2000              # per session; older ones are dropped
MAX_MESSAGE = 16 * 1024 * 1024
MAX_EVENT_TEXT = 256_000       # characters of one message kept in the log
MAX_SESSIONS = 30
_HANDSHAKE_SKIP = {"host", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version", "sec-websocket-protocol",
                   "sec-websocket-extensions", "content-length", "content-type", "transfer-encoding", "accept-encoding"}


class WsError(Exception):
    pass


def normalize_url(url: str) -> str:
    """ws/wss as given; http/https are taken as their WebSocket twins."""
    url = (url or "").strip()
    low = url.lower()
    if low.startswith("http://"):
        return "ws://" + url[7:]
    if low.startswith("https://"):
        return "wss://" + url[8:]
    return url


def is_ws_url(url: str) -> bool:
    return (url or "").strip().lower().startswith(("ws://", "wss://"))


def _frame(opcode: int, payload: bytes) -> bytes:
    """One final, masked frame (a client must mask everything it sends)."""
    head = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += struct.pack("!H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack("!Q", n)
    mask = os.urandom(4)
    head += mask
    return bytes(head) + _xor(payload, mask)


def _xor(payload: bytes, mask: bytes) -> bytes:
    """Mask or unmask: every byte XOR the mask byte at its position modulo 4."""
    n = len(payload)
    if not n:
        return b""
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


class Session:
    def __init__(self, url: str, headers: list[tuple[str, str]] | None = None, subprotocols: list[str] | None = None,
                 verify_ssl: bool = True, timeout: float = 20.0) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.url = normalize_url(url)
        self.headers = list(headers or [])
        self.subprotocols = [s for s in (subprotocols or []) if s]
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.state = "connecting"      # connecting | open | closed | error
        self.subprotocol = ""
        self.close_code: int | None = None
        self.close_reason = ""
        self.created_at = time.time()
        self.closed_at: float | None = None
        self.events: list[dict[str, Any]] = []
        self._n = 0
        self._changed = asyncio.Event()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self._wlock = asyncio.Lock()
        self._close_sent = False

    # ------------------------------------------------------------------ log
    def _log(self, direction: str, kind: str, text: str = "", size: int | None = None) -> dict[str, Any]:
        self._n += 1
        ev = {"n": self._n, "ts": time.time(), "dir": direction, "kind": kind,
              "text": text[:MAX_EVENT_TEXT], "size": len(text.encode("utf-8", "replace")) if size is None else size,
              "truncated": len(text) > MAX_EVENT_TEXT}
        self.events.append(ev)
        if len(self.events) > MAX_EVENTS:
            del self.events[:len(self.events) - MAX_EVENTS]
        self._changed.set()
        return ev

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        return {"id": self.id, "url": self.url, "state": self.state, "subprotocol": self.subprotocol,
                "close_code": self.close_code, "close_reason": self.close_reason, "last": self._n,
                "events": [e for e in self.events if e["n"] > after]}

    async def wait(self, after: int, timeout: float) -> dict[str, Any]:
        """The events after ``after``; when there are none yet, waits up to ``timeout`` seconds for one."""
        deadline = time.monotonic() + max(0.0, timeout)
        while self._n <= after and self.state in ("connecting", "open"):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), left)
            except asyncio.TimeoutError:
                break
        return self.snapshot(after)

    # -------------------------------------------------------------- connect
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await asyncio.wait_for(self._handshake(), self.timeout)
        except asyncio.TimeoutError:
            self._fail(f"No answer within {self.timeout:g} seconds")
            return
        except (WsError, OSError, ssl.SSLError, ValueError) as exc:
            self._fail(str(exc) or type(exc).__name__)
            return
        self.state = "open"
        self._log("info", "open", "Connected" + (f" (subprotocol {self.subprotocol})" if self.subprotocol else ""))
        try:
            await self._read_loop()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            if self.state == "open":
                self._closed(1006, "The connection dropped without a closing handshake")
        except WsError as exc:
            self._fail(str(exc))
        finally:
            self._shutdown()

    def _fail(self, message: str) -> None:
        self.state = "error"
        self.closed_at = time.time()
        self._log("error", "close", message)
        self._shutdown()

    def _closed(self, code: int, reason: str) -> None:
        self.state = "closed"
        self.close_code, self.close_reason = code, reason
        self.closed_at = time.time()
        self._log("info", "close", f"Closed ({code})" + (f": {reason}" if reason else ""))

    def _shutdown(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None
        self._changed.set()

    async def _handshake(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise WsError("A WebSocket address starts with ws:// or wss://")
        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        ctx = None
        if secure:
            ctx = ssl.create_default_context()
            if not self.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        self._reader, self._writer = await asyncio.open_connection(parts.hostname, port, ssl=ctx)
        key = base64.b64encode(os.urandom(16)).decode()
        host = parts.hostname if port in (80, 443) else f"{parts.hostname}:{port}"
        if ":" in parts.hostname:   # IPv6 literal
            host = f"[{parts.hostname}]" + ("" if port in (80, 443) else f":{port}")
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        lines = [f"GET {target} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if self.subprotocols:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(self.subprotocols))
        for name, value in self.headers:
            if name.lower() not in _HANDSHAKE_SKIP and "\n" not in name + value and "\r" not in name + value:
                lines.append(f"{name}: {value}")
        self._writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8", "replace"))
        await self._writer.drain()
        try:
            raw = await self._reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as exc:
            raise WsError("The server closed the connection during the handshake") from exc
        except asyncio.LimitOverrunError as exc:
            raise WsError("The server's handshake answer is too large") from exc
        head = raw.decode("iso-8859-1").split("\r\n")
        status = head[0].split(" ", 2)
        answer = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in head[1:] if ":" in line)}
        if len(status) < 2 or status[1] != "101":
            hint = ""
            if len(status) >= 2 and status[1] in ("301", "302", "307", "308") and answer.get("location"):
                hint = f" (redirects to {answer['location']})"
            elif len(status) >= 2 and status[1] in ("200", "404", "400", "426"):
                hint = ": this address does not speak WebSocket"
            raise WsError(f"The server did not switch to WebSocket: {' '.join(status[1:]) or head[0]}{hint}")
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if answer.get("sec-websocket-accept") != expect:
            raise WsError("The server's handshake answer is not valid (wrong Sec-WebSocket-Accept)")
        self.subprotocol = answer.get("sec-websocket-protocol", "")

    # ----------------------------------------------------------------- read
    async def _read_frame(self) -> tuple[bool, int, bytes]:
        assert self._reader is not None
        b1, b2 = await self._reader.readexactly(2)
        fin, opcode = bool(b1 & 0x80), b1 & 0x0F
        if b1 & 0x70:
            raise WsError("The server sent a compressed or extended frame, which is not supported")
        masked, n = bool(b2 & 0x80), b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", await self._reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", await self._reader.readexactly(8))[0]
        if n > MAX_MESSAGE:
            raise WsError(f"A message of {n} bytes is larger than this client accepts")
        mask = await self._reader.readexactly(4) if masked else b""
        payload = await self._reader.readexactly(n) if n else b""
        if masked:
            payload = _xor(payload, mask)
        return fin, opcode, payload

    async def _read_loop(self) -> None:
        buffer, kind = bytearray(), 0
        while True:
            fin, opcode, payload = await self._read_frame()
            if opcode == 0x8:      # close
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                reason = payload[2:].decode("utf-8", "replace")
                if not self._close_sent:
                    await self._send_raw(0x8, payload[:2])
                self._closed(code, reason)
                return
            if opcode == 0x9:      # ping: answer it
                self._log("in", "ping", payload.decode("utf-8", "replace"), len(payload))
                await self._send_raw(0xA, payload)
                continue
            if opcode == 0xA:
                self._log("in", "pong", payload.decode("utf-8", "replace"), len(payload))
                continue
            if opcode in (0x1, 0x2):
                buffer, kind = bytearray(payload), opcode
            elif opcode == 0x0:
                buffer += payload
                if len(buffer) > MAX_MESSAGE:
                    raise WsError("A fragmented message grew larger than this client accepts")
            else:
                raise WsError(f"Unknown frame type {opcode}")
            if fin:
                self._message(kind, bytes(buffer))
                buffer = bytearray()

    def _message(self, kind: int, data: bytes) -> None:
        if kind == 0x1:
            self._log("in", "text", data.decode("utf-8", "replace"), len(data))
        else:
            self._log("in", "binary", _binary_preview(data), len(data))

    # ----------------------------------------------------------------- send
    async def _send_raw(self, opcode: int, payload: bytes) -> None:
        if self._writer is None:
            raise WsError("Not connected")
        async with self._wlock:
            self._writer.write(_frame(opcode, payload))
            await self._writer.drain()

    async def send_text(self, text: str) -> dict[str, Any]:
        if self.state != "open":
            raise WsError("Not connected")
        data = text.encode("utf-8")
        await self._send_raw(0x1, data)
        return self._log("out", "text", text, len(data))

    async def send_binary(self, data: bytes) -> dict[str, Any]:
        if self.state != "open":
            raise WsError("Not connected")
        await self._send_raw(0x2, data)
        return self._log("out", "binary", _binary_preview(data), len(data))

    async def ping(self) -> dict[str, Any]:
        if self.state != "open":
            raise WsError("Not connected")
        await self._send_raw(0x9, b"pyhttp")
        return self._log("out", "ping", "pyhttp", 6)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Start the closing handshake; the server's answer (or 2 seconds) ends the session."""
        if self.state == "connecting" and self._task:
            self._task.cancel()
            self._closed(1000, "Cancelled before the connection opened")
            self._shutdown()
            return
        if self.state != "open" or self._close_sent:
            return
        self._close_sent = True
        try:
            await self._send_raw(0x8, struct.pack("!H", code) + reason.encode("utf-8")[:120])
        except (WsError, ConnectionError, OSError):
            pass
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass
        if self.state == "open":
            self._closed(code, reason or "closed here")
            self._shutdown()


def _binary_preview(data: bytes) -> str:
    shown = data[:64].hex(" ")
    return shown + (" …" if len(data) > 64 else "")


class Manager:
    """The open WebSocket sessions of the app."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def open(self, url: str, **kwargs: Any) -> Session:
        self._prune()
        session = Session(url, **kwargs)
        self.sessions[session.id] = session
        session.start()
        return session

    def _prune(self) -> None:
        # finished sessions stay readable for a while (the UI still shows their log), oldest go first
        done = sorted((s for s in self.sessions.values() if s.state in ("closed", "error")), key=lambda s: s.closed_at or 0)
        while len(self.sessions) >= MAX_SESSIONS and done:
            self.sessions.pop(done.pop(0).id, None)
        if len(self.sessions) >= MAX_SESSIONS:
            raise WsError(f"{MAX_SESSIONS} WebSocket connections are open; close one first")

    async def close_all(self) -> None:
        for s in list(self.sessions.values()):
            try:
                await s.close(1001, "pyhttp is closing")
            except Exception:  # noqa: BLE001
                pass
        self.sessions.clear()
