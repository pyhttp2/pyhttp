"""Flows: saved requests wired into a small program, drawn as blocks on a canvas.

A flow is a graph. Each block does one thing and leaves through one of its ports; the
wire on that port names the block that runs next. A port with no wire ends the flow.

    block      what it does                                  ports
    start      where a run begins: when Run is pressed, or   out
               when an email arrives that fits
    request    sends a saved request                         ok, fail
    email      waits for an email to the test inbox          received, timeout
    set        puts a value (or part of one) in a variable   out
    if         compares two values                           true, false
    delay      waits                                         out
    log        writes a line to the run log                  out
    stop       ends the flow as passed or failed             (none)

Values are templates: everything a request can use ({{variable}}, {{prev.json.id}},
{{$guid}}) plus what the flow itself made: variables from ``set`` blocks and the last
email as {{email.subject}}, {{email.from}}, {{email.text}}, {{email.code}} (the number
the message calls its code, else its first 6-digit number, else its first number of 4 to
8 digits) and {{email.link}} (the first link). They are passed on to the
requests the flow sends.

The Email block and an email-started Start block say which messages they mean with up to
four texts (from, subject, text, to: each "contains", case ignored, empty = not looked at)
and whether a message must fit all of them or any one of them (``fits`` below). When
no message fitted in time, the log says what did arrive and why the newest one was not it.

A run fails when it ends on an unwired ``fail`` or ``timeout`` port, on a ``stop`` block
set to fail, or when it runs more blocks than ``Limits.steps`` (two blocks wired in a
circle would otherwise run for ever).

Receiving email needs a mail system behind the app. This module only knows the shape of
one (``Mail``). pyhttp on the web has it: every account owns a test inbox there. The desktop
app reads that same inbox from the cloud once it is signed in to the account
(``cloudsync.CloudInbox``); signed out, the Email block says to sign in. A flow starts by
itself on an arriving email only on the web, where something is always running.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from . import db, runner
from .templating import build_context, render

BLOCKS: dict[str, tuple[str, ...]] = {
    "start": ("out",), "request": ("ok", "fail"), "email": ("received", "timeout"), "set": ("out",),
    "if": ("true", "false"), "delay": ("out",), "log": ("out",), "stop": (),
}
FAILING_PORTS = ("fail", "timeout")
OPERATORS = ("equals", "not_equals", "contains", "not_contains", "matches", "greater", "less", "empty", "not_empty")
MAX_BLOCKS = 100          # in one flow
MAX_NAME = 120
MAX_TEXT = 20_000         # characters in one field of a block
MAX_EVENTS = 1000         # per run; a longer log loses its oldest lines
KEPT_RUNS = 20            # finished runs kept in memory for the UI

_NUMBER = r"(?<!\w)(?<!\d\.)(\d{%s})(?!\w|\.\d)"   # 482913 and "482913." but not 1.2345 or abc1234
# the number a message calls its code; failing that a six-digit one; failing that any of 4 to 8 digits
_CODES = (re.compile(r"(?:code|otp|pin|passcode|password|verification)\b\D{0,40}?" + _NUMBER % "4,8", re.I),
          re.compile(_NUMBER % "6"), re.compile(_NUMBER % "4,8"))
_LINK = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass(frozen=True)
class Limits:
    steps: int = 500             # blocks run in one flow run
    wait_seconds: float = 600    # the longest one email or delay block may wait
    active: int = 5              # flow runs going at the same time


class Mail(Protocol):
    def address(self) -> str:
        """The address mail is received on."""

    async def wait(self, match: dict[str, str], since: float, timeout: float) -> dict[str, Any] | None:
        """The first message received after ``since`` that fits ``match`` (``to``, ``from``,
        ``subject``, ``contains``: each a text the field must contain, case ignored), waiting up
        to ``timeout`` seconds for it. None when there is none. A message is a dict with ``id``,
        ``from``, ``to``, ``subject``, ``text`` and ``received_at``. ``match["mode"]`` is "all"
        or "any" (see ``fits``)."""

    def recent(self, since: float = 0.0) -> list[dict[str, Any]]:
        """The messages received after ``since``, newest first."""


# Replaced where pyhttp runs as a service: the mail system, and the limits of the running user.
mail: Callable[[], Mail | None] = lambda: None
limits: Callable[[], Limits] = Limits
# True where pyhttp runs as a service: mail arrives there by itself and can start a flow.
starts_on_email = False
NO_MAIL = ("Sign in to the pyhttp cloud (the Cloud button) to receive email in flows: your test inbox is part of "
           "your account. This block took \u201ctimeout\u201d.")


def email_values(message: dict[str, Any]) -> dict[str, Any]:
    """What {{email.…}} offers for a received message."""
    text = str(message.get("text") or "")
    both = f"{message.get('subject') or ''}\n{text}"
    code = next((hit for hit in (pattern.search(both) for pattern in _CODES) if hit), None)
    link = _LINK.search(text)
    return {"id": message.get("id") or "", "from": message.get("from") or "", "to": message.get("to") or "",
            "subject": message.get("subject") or "", "text": text,
            "code": code.group(1) if code else "", "link": link.group(0).rstrip(".,;") if link else ""}


MATCH_FIELDS = (("from", "from", "From"), ("subject", "subject", "Subject"), ("contains", "text", "Text"), ("to", "to", "To"))


def fits(message: dict[str, Any], match: dict[str, Any]) -> tuple[bool, str]:
    """Whether a message is the one meant, and when not, why not (for the run log).

    ``match`` holds up to four texts: ``from``, ``subject``, ``contains`` (the text of the
    message) and ``to``. Each is looked for inside its field, case ignored; an empty one is
    not looked at. With ``mode`` "any" one of them is enough, otherwise all must be found.
    Nothing filled in fits every message."""
    asked = [(label, str(match.get(key) or "").strip(), str(message.get(field) or ""))
             for key, field, label in MATCH_FIELDS if str(match.get(key) or "").strip()]
    if not asked:
        return True, ""
    missed = [f"{label} does not contain \u201c{want}\u201d" for label, want, have in asked if want.lower() not in have.lower()]
    if str(match.get("mode") or "all") == "any":
        return (True, "") if len(missed) < len(asked) else (False, "none of these fits: " + "; ".join(missed))
    return (True, "") if not missed else (False, "; ".join(missed))


def match_of(config: dict[str, Any], text: Callable[[str], str]) -> dict[str, str]:
    """A block's match settings with their templates filled in."""
    return {**{key: text(key) for key, _field, _label in MATCH_FIELDS}, "mode": "any" if config.get("mode") == "any" else "all"}


def described(match: dict[str, Any]) -> str:
    parts = [f"{label.lower()} contains \u201c{str(match.get(key)).strip()}\u201d" for key, _f, label in MATCH_FIELDS if str(match.get(key) or "").strip()]
    return (" or " if match.get("mode") == "any" else " and ").join(parts) if parts else "any message"


def why_none(system: "Mail", match: dict[str, Any], since: float, waited: float) -> str:
    """What to tell the user when no message fitted: what was asked for, and what did arrive."""
    line = f"No email whose {described(match)} arrived at {system.address()} within {waited:g} s."
    if not any(str(match.get(key) or "").strip() for key, _f, _l in MATCH_FIELDS):
        line = f"No email arrived at {system.address()} within {waited:g} s."
    try:
        seen = system.recent(since)
        earlier = sum(1 for m in system.recent(0.0) if float(m.get("received_at") or 0) <= since and fits(m, match)[0])
    except Exception:  # noqa: BLE001 - the explanation is a courtesy
        return line
    if not seen:
        # the usual surprise: the mail was sent first and the flow started after it
        before = ("" if not earlier else
                  f" {'1 message' if earlier == 1 else f'{earlier} messages'} already in the inbox would fit, but arrived before "
                  f"this block began to wait: it takes only mail that arrives after that. Start the flow, then send the mail.")
        return line + " Nothing was received in that time." + before
    newest = seen[0]
    _ok, why = fits(newest, match)
    count = "1 message" if len(seen) == 1 else f"{len(seen)} messages"
    return (f"{line} {count} did arrive; the newest, \u201c{newest.get('subject') or '(no subject)'}\u201d from "
            f"{newest.get('from') or 'an unknown sender'}, was skipped: {why}.")


def trigger_of(flow: dict[str, Any]) -> dict[str, Any] | None:
    """The Start block's settings when the flow is started by an arriving email, else None."""
    for node in (flow.get("graph") or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "start":
            config = node.get("config") if isinstance(node.get("config"), dict) else {}
            return config if config.get("trigger") == "email" else None
    return None


# ----------------------------------------------------------------------- the graph
def clean_graph(graph: Any) -> dict[str, Any]:
    """The graph as it is stored: known blocks, known ports, wires between blocks that exist.
    Raises ValueError with a message for the user."""
    if not isinstance(graph, dict):
        raise ValueError("A flow is an object with nodes and edges")
    nodes_in, edges_in = graph.get("nodes") or [], graph.get("edges") or []
    if not isinstance(nodes_in, list) or not isinstance(edges_in, list):
        raise ValueError("A flow is an object with nodes and edges")
    if len(nodes_in) > MAX_BLOCKS:
        raise ValueError(f"A flow has at most {MAX_BLOCKS} blocks; this one has {len(nodes_in)}")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes_in:
        if not isinstance(node, dict) or node.get("type") not in BLOCKS:
            raise ValueError(f"Unknown block type: {node.get('type') if isinstance(node, dict) else node!r}")
        nid = str(node.get("id") or "")[:40]
        if not nid or nid in seen:
            raise ValueError("Every block needs an id of its own")
        seen.add(nid)
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        config = {str(k)[:40]: (v if isinstance(v, (bool, int, float)) else str(v if v is not None else "")[:MAX_TEXT])
                  for k, v in list(config.items())[:20]}
        try:
            x, y = float(node.get("x") or 0), float(node.get("y") or 0)
        except (TypeError, ValueError):
            x = y = 0.0
        nodes.append({"id": nid, "type": node["type"], "x": round(x), "y": round(y), "config": config})
    if sum(1 for n in nodes if n["type"] == "start") > 1:
        raise ValueError("A flow has one Start block")
    by_id = {n["id"]: n for n in nodes}
    edges: list[dict[str, str]] = []
    taken: set[tuple[str, str]] = set()
    for edge in edges_in:
        if not isinstance(edge, dict):
            continue
        source, port, target = str(edge.get("from") or ""), str(edge.get("port") or ""), str(edge.get("to") or "")
        if source not in by_id or target not in by_id or port not in BLOCKS[by_id[source]["type"]]:
            continue   # a wire to a block that was deleted
        if (source, port) in taken or by_id[target]["type"] == "start":
            continue   # one wire per port; nothing leads back into Start
        taken.add((source, port))
        edges.append({"from": source, "port": port, "to": target})
    return {"nodes": nodes, "edges": edges}


def new_graph() -> dict[str, Any]:
    return {"nodes": [{"id": "start", "type": "start", "x": 40, "y": 60, "config": {}}], "edges": []}


# ------------------------------------------------------------------------- one run
def _compare(left: str, op: str, right: str) -> bool:
    if op == "empty":
        return left.strip() == ""
    if op == "not_empty":
        return left.strip() != ""
    if op in ("greater", "less"):
        try:
            a, b = float(left), float(right)
        except ValueError:
            return False
        return a > b if op == "greater" else a < b
    if op == "matches":
        try:
            return re.search(right, left) is not None
        except re.error as exc:
            raise ValueError(f"not a valid pattern: {exc}") from exc
    if op in ("contains", "not_contains"):
        return (right.lower() in left.lower()) == (op == "contains")
    return (left.strip() == right.strip()) == (op != "not_equals")


class Run:
    """One run of a flow: its log, readable while it goes."""

    def __init__(self, flow: dict[str, Any], started_by: dict[str, Any] | None = None,
                 mail_system: Callable[[], Mail | None] | None = None) -> None:
        self.id = uuid.uuid4().hex[:16]
        self._mail = mail_system or mail
        self.flow_id = flow["id"]
        self.name = flow["name"]
        self.graph = clean_graph(flow.get("graph") or {})
        self.state = "running"      # running | passed | failed | cancelled
        self.message = ""
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.events: list[dict[str, Any]] = []
        self.task: asyncio.Task[None] | None = None
        self.vars: dict[str, str] = {}       # set by the flow's own blocks
        self.email: dict[str, Any] = {}
        self.prev: dict[str, Any] | None = None
        self._n = 0
        self._changed = asyncio.Event()
        self._mail_since = self.started_at
        self.started_by = started_by         # the email that started this run, if one did
        if started_by:
            self.email = email_values(started_by)
            self._mail_since = float(started_by.get("received_at") or self.started_at)

    # ------------------------------------------------------------------ log
    def _log(self, node: dict[str, Any] | None, status: str, text: str, port: str = "", ms: float | None = None) -> None:
        self._n += 1
        self.events.append({"n": self._n, "at": time.time(), "node": node["id"] if node else "", "type": node["type"] if node else "",
                            "status": status, "port": port, "text": text[:2000], "ms": None if ms is None else round(ms, 1)})
        if len(self.events) > MAX_EVENTS:
            del self.events[:len(self.events) - MAX_EVENTS]
        self._changed.set()

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        return {"id": self.id, "flow_id": self.flow_id, "name": self.name, "state": self.state, "message": self.message,
                "started_at": self.started_at, "ended_at": self.ended_at, "last": self._n,
                "started_by": f"email from {self.email.get('from')}" if self.started_by else "",
                "events": [e for e in self.events if e["n"] > after]}

    async def wait(self, after: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, timeout)
        while self._n <= after and self.state == "running":
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), left)
            except asyncio.TimeoutError:
                break
        return self.snapshot(after)

    # ------------------------------------------------------------------ run
    def _context(self, folder_id: str | None = None) -> dict[str, Any]:
        variables, _env = db.effective_variables(folder_id)   # read again: a request may have extracted into them
        ctx = build_context(variables, self.prev)
        ctx["vars"].update(self.vars)
        ctx["email"] = self.email
        return ctx

    def _text(self, node: dict[str, Any], key: str) -> str:
        return render(str(node["config"].get(key) or ""), self._context())

    async def _go(self, bounds: Limits) -> None:
        nodes = {n["id"]: n for n in self.graph["nodes"]}
        wires = {(e["from"], e["port"]): e["to"] for e in self.graph["edges"]}
        node = next((n for n in self.graph["nodes"] if n["type"] == "start"), None)
        if node is None:
            self._end("failed", "The flow has no Start block")
            return
        steps = 0
        while True:
            steps += 1
            if steps > bounds.steps:
                self._end("failed", f"Stopped after {bounds.steps} blocks: the flow runs in a circle, or is longer than a run may be")
                return
            self._log(node, "running", "")
            began = time.perf_counter()
            try:
                port, status, text = await self._block(node, bounds)
            except Exception as exc:  # noqa: BLE001 - a block that breaks ends the flow with its reason
                self._log(node, "fail", f"{type(exc).__name__}: {exc}", ms=(time.perf_counter() - began) * 1000)
                self._end("failed", f"{type(exc).__name__}: {exc}")
                return
            self._log(node, status, text, port, (time.perf_counter() - began) * 1000)
            if node["type"] == "stop":
                self._end("failed" if status == "fail" else "passed", text)
                return
            following = wires.get((node["id"], port))
            if following is None:
                if port in FAILING_PORTS:
                    self._end("failed", text)
                else:
                    self._end("passed", "")
                return
            node = nodes[following]

    async def _block(self, node: dict[str, Any], bounds: Limits) -> tuple[str, str, str]:
        """(port taken, "ok" or "fail", line for the log)."""
        kind, config = node["type"], node["config"]
        if kind == "start":
            if self.started_by:
                return "out", "ok", f"Started by an email from {self.email['from']}: {self.email['subject']}"
            return "out", "ok", "Started"
        if kind == "request":
            req = db.get_request(str(config.get("request_id") or ""))
            if not req:
                return "fail", "fail", "The request this block sends no longer exists"
            item = runner.expand(req)[0]
            result = await runner.execute(item, self._context(req.get("folder_id")))
            runner.record(result, source=f"Flow: {self.name}", req=item, environment=db.effective_variables()[1])
            self.prev = runner.prev_context(result)
            failed_tests = result.get("tests_failed") or 0
            good = result["status"] != "error" and (result.get("status_code") or 0) < 400 and not failed_tests
            line = f"{item.get('method', '')} {req['name']}: " + (
                str(result.get("error")) if result["status"] == "error" else f"{result.get('status_code')} {result.get('reason') or ''}".strip())
            if failed_tests:
                line += f", {failed_tests} test(s) failed"
            return ("ok", "ok", line) if good else ("fail", "fail", line)
        if kind == "email":
            system = self._mail()
            if system is None:
                return "timeout", "fail", NO_MAIL
            match = match_of(config, lambda key: self._text(node, key))
            try:
                asked = float(config.get("timeout_s") or 60)
            except (TypeError, ValueError):
                asked = 60.0
            waited = max(1.0, min(asked, bounds.wait_seconds))
            message = await system.wait(match, self._mail_since, waited)
            if message is None:
                return "timeout", "fail", why_none(system, match, self._mail_since, waited)
            self._mail_since = float(message.get("received_at") or time.time())   # the next block waits for a newer one
            self.email = email_values(message)
            found = f", code {self.email['code']}" if self.email["code"] else ""
            return "received", "ok", f"Email from {self.email['from']}: {self.email['subject']}{found}"
        if kind == "set":
            name = str(config.get("name") or "").strip()
            if not name:
                return "out", "fail", "This block has no variable name"
            value = self._text(node, "value")
            pattern = str(config.get("pattern") or "")
            if pattern:
                try:
                    hit = re.search(pattern, value, re.S)
                except re.error as exc:
                    raise ValueError(f"not a valid pattern: {exc}") from exc
                value = (hit.group(1) if hit.groups() else hit.group(0)) if hit else ""
            self.vars[name] = value
            if config.get("save"):
                db.set_variable(name, value)
            shown = value if len(value) <= 80 else value[:80] + "…"
            return "out", "ok", f"{name} = {shown}"
        if kind == "if":
            left, right = self._text(node, "left"), self._text(node, "right")
            op = str(config.get("op") or "equals")
            if op not in OPERATORS:
                op = "equals"
            answer = _compare(left, op, right)
            return ("true" if answer else "false"), "ok", f"{left[:60]!r} {op.replace('_', ' ')} {right[:60]!r}: {'yes' if answer else 'no'}"
        if kind == "delay":
            try:
                seconds = float(config.get("seconds") or 1)
            except (TypeError, ValueError):
                seconds = 1.0
            seconds = max(0.0, min(seconds, bounds.wait_seconds))
            await asyncio.sleep(seconds)
            return "out", "ok", f"Waited {seconds:g} s"
        if kind == "log":
            return "out", "ok", self._text(node, "message")
        failing = str(config.get("result") or "pass") == "fail"
        return "", ("fail" if failing else "ok"), self._text(node, "message") or ("Stopped: failed" if failing else "Stopped")

    def _end(self, state: str, message: str) -> None:
        self.state, self.message, self.ended_at = state, message, time.time()
        said = bool(self.events) and self.events[-1]["text"] == message[:2000]   # the block just said it: not twice
        self._log(None, "fail" if state == "failed" else "ok", f"Flow {state}" + (f": {message}" if message and not said else ""))
        try:
            db.update_flow(self.flow_id, last_run_at=self.started_at, last_status=state)
        except Exception:  # noqa: BLE001 - the flow was deleted, or the project closed, while it ran
            pass


class FlowManager:
    """The flow runs of the app. ``mail`` answers this app's own mail system, when it has one
    apart from the module's (the desktop app: the account's inbox in the cloud, once signed in)."""

    def __init__(self, mail: Callable[[], Mail | None] | None = None) -> None:
        self.runs: dict[str, Run] = {}
        self._own_mail = mail

    def mail_system(self) -> Mail | None:
        return (self._own_mail() if self._own_mail else None) or mail()

    def active(self) -> int:
        return sum(1 for run in self.runs.values() if run.state == "running")

    def latest(self, flow_id: str) -> Run | None:
        """The newest run of a flow that is still kept."""
        mine = [run for run in self.runs.values() if run.flow_id == flow_id]
        return max(mine, key=lambda run: run.started_at) if mine else None

    def start_by_email(self, flows_list: list[dict[str, Any]], message: dict[str, Any]) -> list[Run]:
        """Start every flow whose Start block waits for an email like this one. A flow that
        cannot start now (too many running) is skipped: the message stays in the inbox."""
        started = []
        for flow in flows_list:
            config = trigger_of(flow)
            if config is None or not fits(message, match_of(config, lambda key, c=config: str(c.get(key) or "")))[0]:
                continue
            try:
                started.append(self.start(flow, message))
            except ValueError:
                continue
        return started

    def start(self, flow: dict[str, Any], started_by: dict[str, Any] | None = None) -> Run:
        bounds = limits()
        if self.active() >= bounds.active:
            raise ValueError("A flow is still running. Wait until it ends, or stop it, then run this one." if bounds.active == 1
                             else f"{self.active()} flows are running, the most at once. Wait until one ends, or stop it.")
        done = sorted((r for r in self.runs.values() if r.state != "running"), key=lambda r: r.ended_at or 0)
        while len(self.runs) >= KEPT_RUNS and done:
            self.runs.pop(done.pop(0).id, None)
        run = Run(flow, started_by, self.mail_system)
        self.runs[run.id] = run
        run.task = asyncio.create_task(self._run(run, bounds))
        return run

    async def _run(self, run: Run, bounds: Limits) -> None:
        try:
            await run._go(bounds)  # noqa: SLF001
        except asyncio.CancelledError:
            run._end("cancelled", "")  # noqa: SLF001

    def cancel(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if run and run.task and not run.task.done():
            run.task.cancel()
            return True
        return False

    async def stop_all(self) -> None:
        for run in list(self.runs.values()):
            if run.task and not run.task.done():
                run.task.cancel()
