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
    view       shows data (a list from a response) as HTML   submitted, timeout
               and JavaScript, and waits for the person
               running the flow to pick from it
    script     runs JavaScript on what came before and       ok, fail
               hands its result on (prepares the next
               request's body, say)
    stop       ends the flow as passed or failed             (none)

A view block and a script block hand a value to the block after them: the items picked,
the value returned. The next script gets it as ``input``; it is also kept in a variable
(``selected`` and ``result`` unless named otherwise), so {{selected}} (as JSON) and
{{selected[0].id}} work in any request.

A run can wait for input: while a view block waits, its snapshot has ``waiting`` (the data,
the HTML and JavaScript to show it with) and ``Run.answer`` takes what was picked.

Several flows can run one after the other (``FlowManager.start_batch``): the Run selected
button of the flow list.

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

Every finished run is kept in the flow's history (the newest 50 of each flow) with its
whole log: when it ran, what started it, how it ended and what each block said.

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
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from . import db, jsengine, runner
from .templating import _Missing, build_context, render, resolve, to_text

BLOCKS: dict[str, tuple[str, ...]] = {
    "start": ("out",), "request": ("ok", "fail"), "email": ("received", "timeout"), "set": ("out",),
    "if": ("true", "false"), "delay": ("out",), "log": ("out",), "view": ("submitted", "timeout"), "script": ("ok", "fail"),
    "stop": (),
}
FAILING_PORTS = ("fail", "timeout")
MAX_VIEW_ITEMS = 5000     # items of a list shown in a view block
MAX_VIEW_BYTES = 2_000_000
MAX_BATCH = 50            # flows in one "Run selected"
KEPT_BATCHES = 10
_ONE_VALUE = re.compile(r"^\s*\{\{\s*([^{}]+?)\s*\}\}\s*$")
OPERATORS = ("equals", "not_equals", "contains", "not_contains", "matches", "greater", "less", "empty", "not_empty")
MAX_BLOCKS = 100          # in one flow
MAX_NAME = 120
MAX_TEXT = 20_000         # characters in one field of a block
MAX_EVENTS = 1000         # per run; a longer log loses its oldest lines
KEPT_RUNS = 20            # finished runs kept in memory for the UI
KEPT_HISTORY = 50         # finished runs of one flow kept in its history (the database)

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
    input_seconds: float = 1800  # the longest a view block waits for someone to pick


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


def _short(text: str, n: int = 80) -> str:
    return text if len(text) <= n else text[:n] + "…"


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
        self.vars: dict[str, Any] = {}       # set by the flow's own blocks (a script may set lists and objects)
        self.handed: Any = None              # what the last view or script block handed on
        self.waiting: dict[str, Any] | None = None   # while a view block waits for someone to pick
        self._answer: asyncio.Future[dict[str, Any]] | None = None
        self.batch_id = ""                   # set when the run is one of a "Run selected"
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
                "batch_id": self.batch_id, "waiting": self.waiting,
                "events": [e for e in self.events if e["n"] > after]}

    def answer(self, value: Any = None, cancel: bool = False) -> None:
        """What the person picked in the view block this run waits on (or that they closed it)."""
        if not self.waiting or self._answer is None or self._answer.done():
            raise ValueError("This run is not waiting for anything to be picked")
        if not cancel:
            low, high = self.waiting.get("min") or 0, self.waiting.get("max") or 0
            if isinstance(value, list):
                if low and len(value) < low:
                    raise ValueError(f"Pick at least {low}")
                if high and len(value) > high:
                    raise ValueError(f"Pick at most {high}")
            if len(json.dumps(value, default=str)) > MAX_VIEW_BYTES:
                raise ValueError("What was picked is too large to hand on")
        self._answer.set_result({"value": value, "cancel": bool(cancel)})

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

    def _value(self, node: dict[str, Any], key: str) -> Any:
        """A setting as a value, not a text: "{{prev.json.items}}" is that list itself, and a
        text that reads as JSON once filled in is that JSON."""
        raw = str(node["config"].get(key) or "")
        ctx = self._context()
        one = _ONE_VALUE.match(raw)
        if one and not one.group(1).strip().startswith("$"):
            try:
                value = resolve(one.group(1), ctx)
            except _Missing:
                value = None
            if value is not None and not isinstance(value, str):
                return value
        text = render(raw, ctx)
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _given(self) -> Any:
        """What a view or script block works on when it is not told: what the block before handed
        on, else the last response's JSON."""
        if self.handed is not None:
            return self.handed
        return (self.prev or {}).get("json")

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
        if kind == "view":
            return await self._view(node, bounds)
        if kind == "script":
            return await self._script(node)
        if kind == "log":
            return "out", "ok", self._text(node, "message")
        failing = str(config.get("result") or "pass") == "fail"
        return "", ("fail" if failing else "ok"), self._text(node, "message") or ("Stopped: failed" if failing else "Stopped")

    async def _view(self, node: dict[str, Any], bounds: Limits) -> tuple[str, str, str]:
        """Show data with the block's HTML and JavaScript (or as a list to tick), then wait
        for the person running the flow to pick from it."""
        config = node["config"]
        data = self._value(node, "data") if str(config.get("data") or "").strip() else self._given()
        if isinstance(data, dict) and not str(config.get("data") or "").strip():
            # {"items": [...], "total": 15}: the list in it is what there is to pick from
            data = next((v for v in data.values() if isinstance(v, list) and v), data)
        if data is None or data == "":
            return "timeout", "fail", ("Nothing to show: no block before this one handed anything on and no request answered with JSON. "
                                       "Set Data to show, e.g. {{prev.json.items}}")
        note = ""
        if isinstance(data, list) and len(data) > MAX_VIEW_ITEMS:
            note = f" (the first {MAX_VIEW_ITEMS} of {len(data)})"
            data = data[:MAX_VIEW_ITEMS]
        if len(json.dumps(data, default=str)) > MAX_VIEW_BYTES:
            return "timeout", "fail", f"The data is larger than {MAX_VIEW_BYTES // 1_000_000} MB: point the block at the list in it"

        def number(key: str, default: float) -> float:
            try:
                return float(config.get(key) if config.get(key) not in (None, "") else default)
            except (TypeError, ValueError):
                return default

        waited = max(5.0, min(number("timeout_s", 300), bounds.input_seconds))
        multiple = config.get("multiple") is not False and str(config.get("multiple")) != "false"
        self.waiting = {"node": node["id"], "title": str(config.get("title") or "Pick from the list"), "data": data,
                        "html": str(config.get("html") or ""), "js": str(config.get("js") or ""),
                        "multiple": multiple, "min": int(number("min", 0)), "max": int(number("max", 0)) if multiple else 1,
                        "field": str(config.get("field") or "").strip(), "until": time.time() + waited}
        self._answer = asyncio.get_running_loop().create_future()
        count = f"{len(data)} items" if isinstance(data, list) else "the data"
        self._log(node, "input", f"Waiting for you to pick from {count}{note}: open this flow to answer")
        try:
            answer = await asyncio.wait_for(asyncio.shield(self._answer), waited)
        except asyncio.TimeoutError:
            return "timeout", "fail", f"Nothing was picked within {waited:g} s"
        finally:
            self.waiting, self._answer = None, None
        if answer["cancel"]:
            return "timeout", "fail", "Closed without picking"
        value = answer["value"]
        name = str(config.get("name") or "selected").strip() or "selected"
        self.vars[name] = value
        self.handed = value
        if isinstance(value, list):
            told = f"Picked {len(value)}" + (f" of {len(data)}" if isinstance(data, list) else "")
        else:
            told = "Picked " + _short(to_text(value))
        return "submitted", "ok", f"{told} → {{{{{name}}}}}"

    async def _script(self, node: dict[str, Any]) -> tuple[str, str, str]:
        config = node["config"]
        code = str(config.get("code") or "")
        if not code.strip():
            return "fail", "fail", "This block has no script"
        ctx = self._context()
        given = self._value(node, "input") if str(config.get("input") or "").strip() else self._given()
        values = {"input": given, "vars": ctx["vars"], "prev": ctx["prev"], "email": self.email}
        res = await asyncio.to_thread(jsengine.run_flow_script, code, values)
        for key, value in list((res.get("vars") or {}).items())[:100]:
            self.vars[str(key)[:100]] = value
        for line in res.get("logs", [])[:50]:
            self._log(node, "info", line)
        made = [str(k) for k in (res.get("vars") or {})]
        if res.get("error"):
            return "fail", "fail", f"Script error: {res['error']}"
        if res.get("failed") is not None:
            return "fail", "fail", res["failed"] or "The script failed"
        value = res.get("value")
        name = str(config.get("name") or "result").strip() or "result"
        if value is not None:
            self.vars[name] = value
            self.handed = value
            made.insert(0, name)
        told = ("Returned " + _short(to_text(value))) if value is not None else "Done"
        return "ok", "ok", told + (f"; set {', '.join('{{' + m + '}}' for m in made)}" if made else "")

    def _end(self, state: str, message: str) -> None:
        self.state, self.message, self.ended_at = state, message, time.time()
        said = bool(self.events) and self.events[-1]["text"] == message[:2000]   # the block just said it: not twice
        self._log(None, "fail" if state == "failed" else "ok", f"Flow {state}" + (f": {message}" if message and not said else ""))
        try:
            db.update_flow(self.flow_id, last_run_at=self.started_at, last_status=state)
            db.add_flow_run(self.snapshot(), KEPT_HISTORY)   # the flow's history: what ran, when, and what each block said
        except Exception:  # noqa: BLE001 - the flow was deleted, or the project closed, while it ran
            pass


class FlowManager:
    """The flow runs of the app. ``mail`` answers this app's own mail system, when it has one
    apart from the module's (the desktop app: the account's inbox in the cloud, once signed in)."""

    def __init__(self, mail: Callable[[], Mail | None] | None = None) -> None:
        self.runs: dict[str, Run] = {}
        self.batches: dict[str, Batch] = {}
        self._own_mail = mail

    def mail_system(self) -> Mail | None:
        return (self._own_mail() if self._own_mail else None) or mail()

    def active(self) -> int:
        """Runs going, a Run selected counting once however many flows it has."""
        alone = sum(1 for run in self.runs.values() if run.state == "running" and not run.batch_id)
        return alone + sum(1 for batch in self.batches.values() if batch.state == "running")

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
        self._keep_room()
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
        for batch in list(self.batches.values()):
            batch.cancelled = True
            if batch.task and not batch.task.done():
                batch.task.cancel()
        for run in list(self.runs.values()):
            if run.task and not run.task.done():
                run.task.cancel()

    # --------------------------------------------------------- Run selected
    def start_batch(self, flow_list: list[dict[str, Any]], stop_on_fail: bool = False) -> "Batch":
        """Run several flows one after the other (one slot of ``Limits.active`` for them all)."""
        if not flow_list:
            raise ValueError("Tick the flows to run first")
        if len(flow_list) > MAX_BATCH:
            raise ValueError(f"Run at most {MAX_BATCH} flows at once")
        bounds = limits()
        if self.active() >= bounds.active:
            raise ValueError("A flow is still running. Wait until it ends, or stop it, then run these." if bounds.active == 1
                             else f"{self.active()} flows are running, the most at once. Wait until one ends, or stop it.")
        old = sorted((b for b in self.batches.values() if b.state != "running"), key=lambda b: b.ended_at or 0)
        while len(self.batches) >= KEPT_BATCHES and old:
            self.batches.pop(old.pop(0).id, None)
        batch = Batch(flow_list, stop_on_fail)
        self.batches[batch.id] = batch
        batch.task = asyncio.create_task(self._run_batch(batch, bounds))
        return batch

    async def _run_batch(self, batch: "Batch", bounds: Limits) -> None:
        try:
            for i, item in enumerate(batch.items):
                if batch.cancelled:
                    break
                flow = db.get_flow(item["flow_id"])
                if not flow:
                    item.update(state="skipped", message="The flow was deleted")
                    continue
                self._keep_room()
                run = Run(flow, None, self.mail_system)
                run.batch_id = batch.id
                self.runs[run.id] = run
                batch.runs[i] = run
                item["run_id"] = run.id
                run.task = asyncio.create_task(self._run(run, bounds))
                await run.task
                if batch.stop_on_fail and run.state != "passed":
                    for rest in batch.items[i + 1:]:
                        rest.update(state="skipped", message=f"Not run: {flow['name']} did not pass")
                    break
        except asyncio.CancelledError:
            batch.cancelled = True
        finally:
            for item in batch.items:
                if item["state"] == "queued":
                    item.update(state="skipped" if not batch.cancelled else "cancelled")
            batch.end()

    def cancel_batch(self, batch_id: str) -> bool:
        batch = self.batches.get(batch_id)
        if not batch or batch.state != "running":
            return False
        batch.cancelled = True
        for run in batch.runs.values():
            if run.task and not run.task.done():
                run.task.cancel()
        if batch.task and not batch.task.done():
            batch.task.cancel()
        return True

    def _keep_room(self) -> None:
        done = sorted((r for r in self.runs.values() if r.state != "running"), key=lambda r: r.ended_at or 0)
        while len(self.runs) >= KEPT_RUNS and done:
            self.runs.pop(done.pop(0).id, None)


class Batch:
    """Flows run one after the other: what the Run selected button of the flow list starts."""

    def __init__(self, flow_list: list[dict[str, Any]], stop_on_fail: bool) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.items = [{"flow_id": f["id"], "name": f["name"], "run_id": "", "state": "queued", "message": ""} for f in flow_list]
        self.runs: dict[int, Run] = {}
        self.stop_on_fail = bool(stop_on_fail)
        self.state = "running"     # running | passed | failed | cancelled
        self.cancelled = False
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.task: asyncio.Task[None] | None = None

    def end(self) -> None:
        self._sync()
        states = [item["state"] for item in self.items]
        self.state = "cancelled" if self.cancelled else "passed" if all(s == "passed" for s in states) else "failed"
        self.ended_at = time.time()

    def _sync(self) -> None:
        for i, run in self.runs.items():
            self.items[i].update(state=run.state, message=run.message, started_at=run.started_at, ended_at=run.ended_at,
                                 waiting=bool(run.waiting))

    def snapshot(self) -> dict[str, Any]:
        self._sync()
        count = {s: sum(1 for item in self.items if item["state"] == s) for s in ("passed", "failed", "cancelled", "skipped", "running", "queued")}
        return {"id": self.id, "state": self.state, "started_at": self.started_at, "ended_at": self.ended_at,
                "stop_on_fail": self.stop_on_fail, "counts": count, "items": [dict(item) for item in self.items]}
