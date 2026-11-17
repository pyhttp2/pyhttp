"""Flows (pyhttp/flows.py): blocks wired into a small program.

Run: python -m pytest tests/test_flows.py -q
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest
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

    async def wait(self, match: dict[str, str], since: float, timeout: float) -> dict[str, Any] | None:
        self.asked.append(match)
        deadline = time.monotonic() + timeout
        while True:
            for m in self.messages:
                if m["received_at"] > since and all(match[k].lower() in str(m.get(field) or "").lower()
                                                    for k, field in (("from", "from"), ("subject", "subject"), ("contains", "text"), ("to", "to"))):
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
    assert inbox.asked[0] == {"to": "", "from": "no-reply@shop", "subject": "Verify", "contains": ""}
    sent = client.get("/api/history").json()[0]
    assert "code=482913" in sent["url"] and "to=tester+signup@inbox.example" in sent["url"]


def test_without_a_mail_system_the_email_block_says_so(client):  # noqa: F811
    result = run(client, graph([block("email", "mail")], ("start", "out", "mail")))
    assert result["state"] == "failed" and "pyhttp on the web" in result["message"]


def test_what_an_email_offers():
    got = flows.email_values({"id": "1", "from": "a@b.c", "to": "x@y.z", "subject": "Your code: 9034",
                              "text": "Call 1234567890123 or visit http://a.example/x?y=1, then https://b.example."})
    assert got["code"] == "9034" and got["link"] == "http://a.example/x?y=1"
    assert flows.email_values({"text": "no numbers v1.2345 here"})["code"] == ""
