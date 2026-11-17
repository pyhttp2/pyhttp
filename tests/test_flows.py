"""Flows (pyhttp/flows.py): blocks wired into a small program.

Run: python -m pytest tests/test_flows.py -q
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp import flows  # noqa: E402
from tests.test_backend import client, echo_url  # noqa: E402, F401 - fixtures


def block(kind: str, bid: str, **config: Any) -> dict[str, Any]:
    return {"id": bid, "type": kind, "x": 0, "y": 0, "config": config}


def graph(blocks: list[dict[str, Any]], *wires: tuple[str, str, str]) -> dict[str, Any]:
    return {"nodes": [block("start", "start"), *blocks], "edges": [{"from": a, "port": port, "to": b} for a, port, b in wires]}


def run(client: TestClient, flow_graph: dict[str, Any], name: str = "f") -> dict[str, Any]:
    made = client.post("/api/flows", json={"name": name, "graph": flow_graph})
    assert made.status_code == 201, made.text
    started = client.post("/api/flow-runs", json={"flow_id": made.json()["id"]})
    assert started.status_code == 201, started.text
    events: list[dict[str, Any]] = []
    for _ in range(100):
        got = client.get(f"/api/flow-runs/{started.json()['id']}?after={events[-1]['n'] if events else 0}&wait=5").json()
        events += got["events"]
        if got["state"] != "running":
            return {**got, "events": [e for e in events if e["status"] != "running"], "flow": made.json()["id"]}
    raise AssertionError("the flow did not end")


def took(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(e["node"], e["port"]) for e in result["events"] if e["node"]]


def test_a_new_flow_has_a_start_and_the_graph_is_checked(client):  # noqa: F811
    made = client.post("/api/flows", json={"name": "  Sign up  "}).json()
    assert made["name"] == "Sign up" and [n["type"] for n in made["graph"]["nodes"]] == ["start"]
    listing = client.get("/api/flows").json()
    assert [f["id"] for f in listing["flows"]] == [made["id"]] and listing["mail"] == ""

    assert client.put(f"/api/flows/{made['id']}", json={"graph": {"nodes": [block("shell", "x")]}}).status_code == 400
    two_starts = {"nodes": [block("start", "a"), block("start", "b")]}
    assert "one Start" in client.put(f"/api/flows/{made['id']}", json={"graph": two_starts}).json()["detail"]
    many = {"nodes": [block("log", f"l{n}") for n in range(flows.MAX_BLOCKS + 1)]}
    assert "at most" in client.put(f"/api/flows/{made['id']}", json={"graph": many}).json()["detail"]

    # wires that lead nowhere, a second wire on one port and a wire back into Start are dropped
    messy = graph([block("log", "a"), block("log", "b")], ("start", "out", "a"), ("start", "out", "b"), ("a", "out", "gone"),
                  ("a", "nope", "b"), ("a", "out", "start"), ("a", "out", "b"))
    saved = client.put(f"/api/flows/{made['id']}", json={"graph": messy, "name": "renamed"}).json()
    assert saved["name"] == "renamed"
    assert saved["graph"]["edges"] == [{"from": "start", "port": "out", "to": "a"}, {"from": "a", "port": "out", "to": "b"}]
    assert client.delete(f"/api/flows/{made['id']}").json() == {"ok": True}
    assert client.get(f"/api/flows/{made['id']}").status_code == 404


def test_requests_pass_values_along_and_a_condition_decides(client, echo_url):  # noqa: F811
    first = client.post("/api/requests", json={
        "name": "make", "method": "POST", "url": echo_url + "/echo", "body_type": "json",
        "bodies": [{"id": "b", "name": "Default", "body": '{"order": 4711}'}]}).json()
    second = client.post("/api/requests", json={"name": "read", "method": "GET", "url": echo_url + "/echo?tag={{tag}}"}).json()
    missing = client.post("/api/requests", json={"name": "gone", "method": "GET", "url": echo_url + "/status/404"}).json()
    result = run(client, graph(
        [block("request", "make", request_id=first["id"]),
         block("set", "tag", name="tag", value="{{prev.json.body}}", pattern=r'"order": (\d+)'),
         block("request", "read", request_id=second["id"]),
         block("if", "check", left="{{prev.json.query.tag}}", op="equals", right="4711"),
         block("request", "gone", request_id=missing["id"]),
         block("log", "said", message="tag was {{tag}}, last status {{prev.status}}"),
         block("stop", "bad", result="fail", message="the tag was wrong")],
        ("start", "out", "make"), ("make", "ok", "tag"), ("tag", "out", "read"), ("read", "ok", "check"),
        ("check", "true", "gone"), ("check", "false", "bad"), ("gone", "fail", "said")))
    assert took(result) == [("start", "out"), ("make", "ok"), ("tag", "out"), ("read", "ok"), ("check", "true"), ("gone", "fail"), ("said", "out")]
    assert result["state"] == "passed"                       # the failing request's "fail" port was wired
    lines = {e["node"]: e["text"] for e in result["events"]}
    assert lines["tag"] == "tag = 4711" and lines["said"] == "tag was 4711, last status 404"
    assert "404" in lines["gone"] and "200" in lines["read"]
    sent = [h["source"] for h in client.get("/api/history").json()]
    assert sent.count("Flow: f") == 3                         # what a flow sends is in the history
    assert client.get(f"/api/flows/{result['flow']}").json()["last_status"] == "passed"


def test_how_a_flow_fails(client, echo_url):  # noqa: F811
    bad = client.post("/api/requests", json={"name": "bad", "method": "GET", "url": echo_url + "/status/500"}).json()
    unwired = run(client, graph([block("request", "r", request_id=bad["id"])], ("start", "out", "r")))
    assert unwired["state"] == "failed" and "500" in unwired["message"]          # ended on an unwired "fail"
    stopped = run(client, graph([block("stop", "s", result="fail", message="no {{nothing}}")], ("start", "out", "s")))
    assert stopped["state"] == "failed" and stopped["message"] == "no {{nothing}}"
    deleted = run(client, graph([block("request", "r", request_id="none")], ("start", "out", "r")))
    assert deleted["state"] == "failed" and "no longer exists" in deleted["message"]
    pattern = run(client, graph([block("if", "i", left="a", op="matches", right="(")], ("start", "out", "i")))
    assert pattern["state"] == "failed" and "not a valid pattern" in pattern["message"]
    assert run(client, graph([]))["state"] == "passed"                           # a Start alone


def test_a_circle_ends_and_a_run_can_be_stopped(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flows, "limits", lambda: flows.Limits(steps=25, wait_seconds=0.05, active=1))
    circle = run(client, graph([block("log", "a", message="again"), block("delay", "b", seconds=0)],
                               ("start", "out", "a"), ("a", "out", "b"), ("b", "out", "a")))
    assert circle["state"] == "failed" and "after 25 blocks" in circle["message"]
    waited = run(client, graph([block("delay", "d", seconds=3600)], ("start", "out", "d")))
    assert waited["state"] == "passed" and waited["events"][1]["text"] == "Waited 0.05 s"   # capped by the limit

    monkeypatch.setattr(flows, "limits", lambda: flows.Limits(steps=25, wait_seconds=600, active=1))
    slow = client.post("/api/flows", json={"name": "slow", "graph": graph([block("delay", "d", seconds=600)], ("start", "out", "d"))}).json()
    first = client.post("/api/flow-runs", json={"flow_id": slow["id"]}).json()
    second = client.post("/api/flow-runs", json={"flow_id": slow["id"]})
    assert second.status_code == 400 and "still running" in second.json()["detail"]
    assert client.post(f"/api/flow-runs/{first['id']}/cancel").json() == {"cancelled": True}
    for _ in range(50):
        state = client.get(f"/api/flow-runs/{first['id']}?wait=1").json()["state"]
        if state != "running":
            break
        time.sleep(0.05)
    assert state == "cancelled"
    assert client.get("/api/flow-runs/unknown").status_code == 404


class Inbox:
    """A mail system for the test: messages are put in by hand."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.asked: list[dict[str, str]] = []

    def address(self) -> str:
        return "tester@inbox.example"

    def recent(self, since: float = 0.0) -> list[dict[str, Any]]:
        return sorted((m for m in self.messages if m["received_at"] > since), key=lambda m: -m["received_at"])

    async def wait(self, match: dict[str, str], since: float, timeout: float) -> dict[str, Any] | None:
        self.asked.append(match)
        deadline = time.monotonic() + timeout
        while True:
            for m in reversed(self.recent(since)):
                if flows.fits(m, match)[0]:
                    return m
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.02)


def test_a_flow_waits_for_an_email_and_uses_what_is_in_it(client, echo_url, monkeypatch):  # noqa: F811
    inbox = Inbox()
    monkeypatch.setattr(flows, "mail", lambda: inbox)
    monkeypatch.setattr(flows, "limits", lambda: flows.Limits(steps=50, wait_seconds=2, active=5))
    assert client.get("/api/flows").json()["mail"] == "tester@inbox.example"
    verify = client.post("/api/requests", json={"name": "verify", "method": "GET",
                                                "url": echo_url + "/echo?code={{email.code}}&to={{email.to}}"}).json()
    flow = graph(
        [block("email", "mail", **{"from": "no-reply@shop", "subject": "{{word}}", "timeout_s": 30}),
         block("request", "verify", request_id=verify["id"]),
         block("set", "link", name="link", value="{{email.link}}"),
         block("email", "again", timeout_s=0.2),
         block("log", "none", message="no second mail; link {{link}}")],
        ("start", "out", "mail"), ("mail", "received", "verify"), ("verify", "ok", "link"), ("link", "out", "again"), ("again", "timeout", "none"))
    client.put("/api/variables", json={"variables": {"word": "Verify"}})
    now = time.time()
    inbox.messages = [
        {"id": "old", "from": "no-reply@shop.example", "to": "tester@inbox.example", "subject": "Verify", "text": "code 111111", "received_at": now - 60},
        {"id": "other", "from": "news@shop.example", "to": "tester@inbox.example", "subject": "Verify", "text": "code 222222", "received_at": now + 5},
        {"id": "it", "from": "No-Reply@Shop.example", "to": "tester+signup@inbox.example", "subject": "Verify your address",
         "text": "Your code is 482913.\nOr open https://shop.example/verify?t=abc. It expires in 2024.", "received_at": now + 6},
    ]
    result = run(client, flow)
    assert result["state"] == "passed", result
    assert took(result) == [("start", "out"), ("mail", "received"), ("verify", "ok"), ("link", "out"), ("again", "timeout"), ("none", "out")]
    lines = {e["node"]: e["text"] for e in result["events"]}
    assert "code 482913" in lines["mail"] and lines["none"] == "no second mail; link https://shop.example/verify?t=abc"
    assert inbox.asked[0] == {"to": "", "from": "no-reply@shop", "subject": "Verify", "contains": "", "mode": "all"}
    sent = client.get("/api/history").json()[0]
    assert "code=482913" in sent["url"] and "to=tester+signup@inbox.example" in sent["url"]


def test_without_a_mail_system_the_email_block_says_so(client):  # noqa: F811
    listing = client.get("/api/flows").json()
    assert listing["mail"] == "" and listing["mail_state"] == "sign_in" and listing["email_start"] is False
    result = run(client, graph([block("email", "mail")], ("start", "out", "mail")))
    assert result["state"] == "failed" and "Sign in to the pyhttp cloud" in result["message"]
    assert client.get("/api/mail").status_code == 404 and "Sign in" in client.get("/api/mail").json()["detail"]


class FakeAccount:
    """A signed-in desktop's sync client, as far as the inbox needs it: the cloud is a dict."""

    def __init__(self) -> None:
        self.token = "t1"
        self.down = False
        self.calls: list[tuple[str, str]] = []
        self.messages = [{"id": "aaaaaaaaaaaaaaaa", "from": "no-reply@shop.example", "to": "me@inbox.pyhttp.com",
                          "subject": "Verify", "text": "Your code is 482913.", "received_at": time.time() + 30, "size": 10}]

    def signed_in(self) -> bool:
        return bool(self.token)

    def _token(self) -> str:
        return self.token

    def _call(self, method: str, path: str, body: Any = None, authed: bool = True, allow: tuple[int, ...] = ()) -> Any:
        from pyhttp import cloudsync
        self.calls.append((method, path))
        if self.down:
            raise cloudsync.CloudError("The pyhttp cloud could not be reached (ConnectError)")
        if path.startswith("/api/sync/~inbox/wait"):
            hit = next((m for m in self.messages if m["received_at"] > body["since"] and flows.fits(m, body["match"])[0]), None)
            return {"message": hit}
        if method == "GET" and path.startswith("/api/sync/~inbox?") or path == "/api/sync/~inbox":
            return {"address": "me@inbox.pyhttp.com", "kept": 50, "messages": list(self.messages)}
        found = next((m for m in self.messages if path.endswith("/" + m["id"])), None)
        if found is None:
            return None
        if method == "DELETE":
            self.messages.remove(found)
            return {"ok": True}
        return found


def test_a_signed_in_desktop_reads_the_accounts_inbox_from_the_cloud(client, echo_url):  # noqa: F811
    from pyhttp import cloudsync

    account = FakeAccount()
    inbox = cloudsync.CloudInbox(account)  # type: ignore[arg-type]
    manager = client.app.state.flow_runs
    manager._own_mail = lambda: inbox if account.signed_in() else None

    listing = client.get("/api/flows").json()
    assert listing["mail"] == "me@inbox.pyhttp.com" and listing["mail_state"] == "ready" and listing["email_start"] is False
    use = client.post("/api/requests", json={"name": "use", "method": "GET", "url": echo_url + "/echo?code={{email.code}}"}).json()
    result = run(client, graph([block("email", "mail", subject="verify", timeout_s=5), block("request", "use", request_id=use["id"])],
                               ("start", "out", "mail"), ("mail", "received", "use")))
    assert result["state"] == "passed" and "code=482913" in client.get("/api/history").json()[0]["url"]
    assert ("POST", "/api/sync/~inbox/wait") in account.calls

    # the inbox itself, and the check of a block's lines, work through the same account
    assert [m["subject"] for m in client.get("/api/mail").json()["messages"]] == ["Verify"]
    assert client.get("/api/mail/aaaaaaaaaaaaaaaa").json()["text"] == "Your code is 482913."
    assert client.get("/api/mail/bbbbbbbbbbbbbbbb").status_code == 404
    checked = client.post("/api/flows/mail-check", json={"match": {"from": "bank"}}).json()
    assert [(m["fits"], m["why"]) for m in checked["messages"]] == [(False, "From does not contain \u201cbank\u201d")]
    assert client.delete("/api/mail/aaaaaaaaaaaaaaaa").json() == {"ok": True} and account.messages == []

    # no mail in time: the same explanation as on the web
    empty = run(client, graph([block("email", "mail", timeout_s=0.2)], ("start", "out", "mail")))
    assert empty["state"] == "failed" and "No email arrived at me@inbox.pyhttp.com" in empty["message"]

    # the cloud does not answer: said plainly, in the list and in the run
    account.down = True
    inbox._address = ("", "")
    listing = client.get("/api/flows").json()
    assert listing["mail_state"] == "offline" and "could not be reached" in listing["mail_problem"]
    assert client.get("/api/mail").status_code == 502
    failed = run(client, graph([block("email", "mail", timeout_s=1)], ("start", "out", "mail")))
    assert failed["state"] == "failed" and "could not be reached" in failed["message"]

    # signed out again: back to "sign in"
    account.down, account.token = False, ""
    assert client.get("/api/flows").json()["mail_state"] == "sign_in"
    out = run(client, graph([block("email", "mail")], ("start", "out", "mail")))
    assert "Sign in to the pyhttp cloud" in out["message"]


def test_what_an_email_offers():
    got = flows.email_values({"id": "1", "from": "a@b.c", "to": "x@y.z", "subject": "Your code: 9034",
                              "text": "Call 1234567890123 or visit http://a.example/x?y=1, then https://b.example."})
    assert got["code"] == "9034" and got["link"] == "http://a.example/x?y=1"
    assert flows.email_values({"text": "no numbers v1.2345 here"})["code"] == ""


def test_all_of_these_or_any_of_these():
    message = {"from": "No-Reply@Shop.example", "to": "me+signup@inbox.example", "subject": "Verify your address", "text": "Your code is 482913."}
    assert flows.fits(message, {}) == (True, "")                                            # nothing asked: every message
    assert flows.fits(message, {"from": "", "subject": "  ", "mode": "any"}) == (True, "")
    assert flows.fits(message, {"from": "shop", "subject": "VERIFY", "contains": "code", "to": "+signup"})[0]
    # the mistake that is easy to make: the same word in two fields, and only one of them has it
    ok, why = flows.fits(message, {"from": "verify", "subject": "verify"})
    assert not ok and why == "From does not contain \u201cverify\u201d"
    assert flows.fits(message, {"from": "verify", "subject": "verify", "mode": "any"}) == (True, "")
    ok, why = flows.fits(message, {"from": "bank", "contains": "invoice", "mode": "any"})
    assert not ok and why.startswith("none of these fits: From does not contain") and "Text does not contain" in why
    assert flows.described({"from": "a", "subject": "b", "mode": "any"}) == "from contains \u201ca\u201d or subject contains \u201cb\u201d"
    assert flows.described({"from": "a", "subject": "b"}) == "from contains \u201ca\u201d and subject contains \u201cb\u201d"
    assert flows.described({}) == "any message"


def test_a_timeout_says_what_arrived_and_why_it_was_not_it(client, monkeypatch):  # noqa: F811
    inbox = Inbox()
    monkeypatch.setattr(flows, "mail", lambda: inbox)
    monkeypatch.setattr(flows, "limits", lambda: flows.Limits(steps=50, wait_seconds=0.3, active=5))
    now = time.time()
    inbox.messages = [{"id": "a", "from": "no-reply@shop.example", "to": "tester@inbox.example", "subject": "Test order", "text": "hi", "received_at": now + 5}]
    both = run(client, graph([block("email", "mail", **{"from": "test", "subject": "test", "timeout_s": 0.3})], ("start", "out", "mail")))
    assert both["state"] == "failed"
    assert "No email whose from contains \u201ctest\u201d and subject contains \u201ctest\u201d arrived" in both["message"]
    assert both["events"][-1]["text"] == "Flow failed"                      # the reason is said once, by the block
    assert "1 message did arrive" in both["message"] and "\u201cTest order\u201d from no-reply@shop.example" in both["message"]
    assert "was skipped: From does not contain \u201ctest\u201d" in both["message"]

    either = run(client, graph([block("email", "mail", **{"from": "test", "subject": "test", "mode": "any", "timeout_s": 0.3})], ("start", "out", "mail")))
    assert either["state"] == "passed" and "Test order" in either["events"][1]["text"]

    inbox.messages = []
    nothing = run(client, graph([block("email", "mail", timeout_s=0.3)], ("start", "out", "mail")))
    assert "No email arrived at tester@inbox.example within 1 s. Nothing was received in that time." == nothing["message"]

    # sent first, flow started after: the block says that this is why
    inbox.messages = [{"id": "o", "from": "test@shop.example", "subject": "Test", "text": "", "received_at": now - 30}]
    late = run(client, graph([block("email", "mail", subject="test", timeout_s=0.3)], ("start", "out", "mail")))
    assert "1 message already in the inbox would fit, but arrived before" in late["message"]
    assert "Start the flow, then send the mail." in late["message"]

    # the same check before a run: which messages in the inbox would a block take
    inbox.messages = [{"id": "a", "from": "no-reply@shop.example", "subject": "Test order", "text": "", "received_at": now},
                      {"id": "b", "from": "test@bank.example", "subject": "Statement", "text": "", "received_at": now + 1}]
    checked = client.post("/api/flows/mail-check", json={"match": {"from": "test", "subject": "test"}}).json()
    assert checked["available"] and [(m["id"], m["fits"]) for m in checked["messages"]] == [("b", False), ("a", False)]
    assert checked["messages"][0]["why"] == "Subject does not contain \u201ctest\u201d"
    any_of = client.post("/api/flows/mail-check", json={"match": {"from": "test", "subject": "test", "mode": "any"}}).json()
    assert [m["fits"] for m in any_of["messages"]] == [True, True]
    monkeypatch.setattr(flows, "mail", lambda: None)
    assert client.post("/api/flows/mail-check", json={"match": {}}).json() == {"available": False, "messages": []}


def test_an_arriving_email_starts_the_flows_that_wait_for_it(client, echo_url, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flows, "limits", lambda: flows.Limits(steps=50, wait_seconds=1, active=5))
    use = client.post("/api/requests", json={"name": "use", "method": "GET", "url": echo_url + "/echo?code={{email.code}}"}).json()

    def flow(name: str, **start: Any) -> str:
        g = {"nodes": [block("start", "start", **start), block("request", "use", request_id=use["id"]),
                       block("log", "said", message="{{email.subject}} / {{email.code}}")],
             "edges": [{"from": "start", "port": "out", "to": "use"}, {"from": "use", "port": "ok", "to": "said"}]}
        return client.post("/api/flows", json={"name": name, "graph": g}).json()["id"]

    orders = flow("orders", trigger="email", subject="order", **{"from": "shop"})
    either = flow("either", trigger="email", subject="invoice", contains="order", mode="any")
    by_hand = flow("by hand")
    other = flow("other", trigger="email", subject="invoice")
    manager = client.app.state.flow_runs
    message = {"id": "m1", "from": "no-reply@shop.example", "to": "t@inbox.example", "subject": "Your order 4471",
               "text": "Order 4471 is confirmed. Code 882211.", "received_at": time.time()}

    async def arrive() -> list[str]:
        from pyhttp import db
        started = manager.start_by_email(db.list_flows(), message)
        for one in started:
            await one.task
        return [one.flow_id for one in started]

    started = client.portal.call(arrive)
    assert started == [orders, either]                                  # not the one run by hand, not the one for invoices
    last = client.get(f"/api/flows/{orders}/last-run").json()["run"]
    assert last["state"] == "passed" and last["started_by"] == "email from no-reply@shop.example"
    lines = [e["text"] for e in last["events"] if e["status"] != "running"]
    assert lines[0] == "Started by an email from no-reply@shop.example: Your order 4471"
    assert lines[2] == "Your order 4471 / 882211"
    assert "code=882211" in client.get("/api/history").json()[0]["url"]
    assert client.get(f"/api/flows/{by_hand}/last-run").json() == {"run": None}
    assert client.get(f"/api/flows/{other}/last-run").json() == {"run": None}


def test_flows_travel_with_an_export(client, echo_url):  # noqa: F811
    rid = client.post("/api/requests", json={"name": "ping", "method": "GET", "url": echo_url + "/echo"}).json()["id"]
    g = graph([block("request", "r", request_id=rid), block("log", "l", message="done")], ("start", "out", "r"), ("r", "ok", "l"))
    client.post("/api/flows", json={"name": "Smoke", "graph": g})
    exported = client.get("/api/export").json()
    assert [f["name"] for f in exported["flows"]] == ["Smoke"]

    done = client.post("/api/import", json={"requests": exported["requests"], "flows": exported["flows"] + [{"name": "broken", "graph": {"nodes": [{"type": "x"}]}}]}).json()
    assert done["flows"] == 1 and done["imported"] == 1
    copies = [f for f in client.get("/api/flows").json()["flows"] if f["name"] == "Smoke"]
    assert len(copies) == 2
    new_id = next(n["config"]["request_id"] for n in copies[1]["graph"]["nodes"] if n["type"] == "request")
    assert new_id != rid and client.get(f"/api/requests/{new_id}").json()["name"] == "ping"   # wired to the imported copy
    result = client.post("/api/flow-runs", json={"flow_id": copies[1]["id"]}).json()
    for _ in range(50):
        got = client.get(f"/api/flow-runs/{result['id']}?after=99&wait=1").json()
        if got["state"] != "running":
            break
    assert got["state"] == "passed"
