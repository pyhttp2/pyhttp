"""Backend checks: run with `python -m pytest tests -q` (or `python tests/test_backend.py`).

A tiny echo server runs in a thread so the runner sends real HTTP requests.
Every test uses a fresh temporary data directory.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp import assertions, curl, db, jsonc, postman, templating  # noqa: E402
from pyhttp.app import create_app, parse_data_file  # noqa: E402

# ------------------------------------------------------------------ echo server
echo = FastAPI()


@echo.api_route("/echo", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def echo_view(request: Request) -> dict:
    body = await request.body()
    return {
        "method": request.method, "url": str(request.url), "headers": dict(request.headers),
        "body": body.decode("utf-8", errors="replace"), "query": dict(request.query_params),
        "cookies": dict(request.cookies),
    }


HOOKS: list = []   # webhook payloads received by /hook (monitor alert tests)


@echo.post("/hook")
async def hook(request: Request) -> dict:
    HOOKS.append(await request.json())
    return {"ok": True}


@echo.get("/sse")
async def sse(n: int = 4) -> Response:
    """A small Server-Sent Events stream: n events, 50 ms apart."""
    import asyncio as _asyncio

    from fastapi.responses import StreamingResponse as _SR

    async def gen():
        for i in range(n):
            yield f"data: tick {i}\n\n"
            await _asyncio.sleep(0.05)

    return _SR(gen(), media_type="text/event-stream")


@echo.get("/set-cookie")
def set_cookie(response: Response) -> dict:
    response.set_cookie("session", "abc123", path="/")
    return {"ok": True}


@echo.get("/slow")
async def slow(seconds: float = 3.0) -> dict:
    import asyncio as _asyncio
    await _asyncio.sleep(seconds)
    return {"slept": seconds}


@echo.get("/redirect")
def redirect() -> Response:
    return Response(status_code=302, headers={"Location": "/echo?from=redirect"})


@echo.post("/post-only")
async def post_only(request: Request) -> dict:
    return {"method": request.method}


@echo.get("/status/{code}")
def status(code: int) -> Response:
    return Response(status_code=code, content=json.dumps({"code": code}), media_type="application/json")


@echo.get("/png")
def png() -> Response:
    return Response(content=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="), media_type="image/png")


FAKE_EXE = b"not really an exe, but " * 3000


@echo.get("/release/pyhttp-9.9.9-windows-x64.exe")
def release_exe() -> Response:
    return Response(content=FAKE_EXE, media_type="application/octet-stream")


@echo.get("/release/SHA256SUMS.txt")
def release_sums() -> Response:
    import hashlib
    good = hashlib.sha256(FAKE_EXE).hexdigest()
    return Response(content=f"{good}  pyhttp-9.9.9-windows-x64.exe\n{'0' * 64}  pyhttp-9.9.9-windows-x64-setup.exe\n", media_type="text/plain")


@echo.get("/digest")
def digest(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Digest "):
        return Response(content=json.dumps({"auth": auth[:40]}), media_type="application/json")
    return Response(status_code=401, headers={"WWW-Authenticate": 'Digest realm="t", nonce="abc", qop="auth"'})


OAUTH: dict = {"issued": [], "revoked": set(), "calls": []}


@echo.post("/oauth/token")
async def oauth_token(request: Request) -> Response:
    from urllib.parse import parse_qsl
    form = dict(parse_qsl((await request.body()).decode()))
    auth = request.headers.get("authorization", "")
    client = base64.b64decode(auth[6:]).decode() if auth.startswith("Basic ") else f"{form.get('client_id')}:{form.get('client_secret')}"
    OAUTH["calls"].append({"grant": form.get("grant_type"), "client": client, "form": form})
    if client != "cid:csecret" or (form.get("grant_type") == "password" and form.get("password") != "pw"):
        return Response(status_code=401, content='{"error": "invalid_client"}', media_type="application/json")
    token = f"tok{len(OAUTH['issued']) + 1}"
    OAUTH["issued"].append(token)
    return Response(content=json.dumps({"access_token": token, "token_type": "Bearer", "expires_in": 3600,
                                        "scope": form.get("scope", "")}), media_type="application/json")


@echo.get("/protected")
def protected(request: Request) -> Response:
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.startswith("Bearer ") else ""
    if tok not in OAUTH["issued"] or tok in OAUTH["revoked"]:
        return Response(status_code=401)
    return Response(content=json.dumps({"token": tok}), media_type="application/json")


@pytest.fixture(scope="session")
def echo_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(echo, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app = create_app(Path(tmp))
        with TestClient(app) as c:
            yield c
        db.close()


def _wait_run(client: TestClient, run_id: str) -> dict:
    for _ in range(200):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# ------------------------------------------------------------------ unit tests
def test_strip_comments_respects_strings():
    text = '{"url": "http://x//y", // trailing\n"b": /* inline */ 2,\n// "c": 3\n"d": "a /* not */ b"}'
    out = jsonc.strip_comments(text)
    assert json.loads(out) == {"url": "http://x//y", "b": 2, "d": "a /* not */ b"}
    assert out.count("\n") == text.count("\n")
    assert jsonc.has_comments(text) and not jsonc.has_comments('{"a": "//"}')
    assert jsonc.strip_comments('"esc\\"//x"') == '"esc\\"//x"'


def test_dynamic_variables():
    ctx = templating.build_context({"name": "x"})
    out = templating.render("{{$guid}}|{{$timestamp}}|{{$randomInt 3 3}}|{{name}}|{{missing}}|{{$nope}}", ctx)
    guid, ts, rnd, name, missing, nope = out.split("|")
    assert len(guid) == 36 and ts.isdigit() and rnd == "3" and name == "x" and missing == "{{missing}}" and nope == "{{$nope}}"
    assert templating.unresolved("{{a}} {{b}} {{$guid}} {{a}}", templating.build_context({"a": "1"})) == ["b"]
    assert len(templating.render("{{$randomString 7}}", ctx)) == 7
    assert templating.render("{{data.email}}", templating.build_context({}, None, {"email": "e@x"})) == "e@x"


def test_assertions_and_extract():
    result = {"status": "ok", "status_code": 201, "duration_ms": 120.0, "error": None,
              "response": {"headers": [("Content-Type", "application/json"), ("X-Id", "42")],
                           "body_text": '{"data": {"token": "t0k", "items": [1, 2, 3], "ok": true, "n": null}}'}}
    tests = [
        {"type": "status", "op": "equals", "value": "201"},
        {"type": "status", "op": "is_2xx"},
        {"type": "status", "op": "in", "value": "200, 201"},
        {"type": "time", "op": "less_than", "value": "500"},
        {"type": "header", "target": "content-type", "op": "contains", "value": "json"},
        {"type": "header", "target": "X-Missing", "op": "not_exists"},
        {"type": "body", "op": "is_json"},
        {"type": "body", "op": "contains", "value": "t0k"},
        {"type": "json", "target": "data.token", "op": "equals", "value": "t0k"},
        {"type": "json", "target": "data.items", "op": "length_equals", "value": "3"},
        {"type": "json", "target": "data.items[1]", "op": "equals", "value": "2"},
        {"type": "json", "target": "data.ok", "op": "is_true"},
        {"type": "json", "target": "data.n", "op": "is_null"},
        {"type": "json", "target": "data.items", "op": "type_is", "value": "array"},
        {"type": "json", "target": "data.items", "op": "contains", "value": "3"},
        {"type": "json", "target": "$.data.token", "op": "matches", "value": "^t0"},
        {"type": "json", "target": "data.nope", "op": "not_exists"},
        # failing ones
        {"type": "status", "op": "equals", "value": "200"},
        {"type": "json", "target": "data.nope", "op": "exists"},
        {"type": "header", "target": "X-Id", "op": "equals", "value": "1"},
        {"type": "json", "target": "data.token", "op": "equals", "value": "x", "enabled": False},
    ]
    out = assertions.run_tests(tests, result)
    assert len(out) == 20
    assert [t["passed"] for t in out[:17]] == [True] * 17
    assert [t["passed"] for t in out[17:]] == [False, False, False]
    assert out[0]["name"] == "Status code equals 201"
    assert out[8]["name"] == "JSON data.token equals t0k"
    ext = assertions.extract([
        {"name": "token", "source": "json", "path": "data.token", "scope": "env"},
        {"name": "xid", "source": "header", "path": "x-id", "scope": "global"},
        {"name": "code", "source": "status", "path": "", "scope": "global"},
        {"name": "first", "source": "regex", "path": r'"items": \[(\d)', "scope": "global"},
        {"name": "items", "source": "json", "path": "data.items", "scope": "global"},
        {"name": "missing", "source": "json", "path": "data.zzz", "scope": "global"},
    ], result)
    assert [(e["name"], e["value"], e["ok"]) for e in ext] == [
        ("token", "t0k", True), ("xid", "42", True), ("code", "201", True), ("first", "1", True),
        ("items", "[1, 2, 3]", True), ("missing", None, False)]
    # an error result still evaluates (and fails) status tests
    err = {"status": "error", "status_code": None, "duration_ms": 5, "error": "boom", "response": None}
    out = assertions.run_tests([{"type": "status", "op": "equals", "value": "200"}], err)
    assert out[0]["passed"] is False and "boom" in out[0]["note"]


def test_curl_parse():
    r = curl.parse("curl 'https://api.example.com/v1/users?page=2' -H 'Accept: application/json' -u bob:secret --compressed")
    assert r["method"] == "GET" and r["url"] == "https://api.example.com/v1/users"
    assert r["params"] == [{"key": "page", "value": "2", "enabled": True}]
    assert r["auth"] == {"type": "basic", "username": "bob", "password": "secret"}
    r = curl.parse("curl -X PUT http://h/x -d 'a=1&b=2'")
    assert r["method"] == "PUT" and r["body_type"] == "form" and r["body"] == "a: 1\nb: 2"
    r = curl.parse('curl http://h/x --data-raw \'{"a":1}\' -H "Authorization: Bearer tok"')
    assert r["method"] == "POST" and r["body_type"] == "json" and r["auth"]["type"] == "bearer" and r["auth"]["token"] == "tok"
    assert not any(h["key"].lower() == "authorization" for h in r["headers"])
    r = curl.parse("curl http://h/upload -F file=@/tmp/a.png -F name=x")
    assert r["body_type"] == "multipart" and r["body"] == "file: @/tmp/a.png\nname: x"
    r = curl.parse("curl --location --request POST 'http://h/a' \\\n --header 'X: 1' \\\n --data '{}'")
    assert r["headers"][0]["key"] == "X" and r["body_type"] == "json"
    with pytest.raises(ValueError):
        curl.parse("curl -H 'a: b'")


def test_postman_roundtrip():
    coll = {
        "info": {"name": "Demo", "schema": postman.SCHEMA},
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}"}]},
        "variable": [{"key": "base", "value": "https://x"}],
        "item": [
            {"name": "Users", "item": [
                {"name": "List", "request": {"method": "GET", "url": {"raw": "{{base}}/users?limit=5",
                                                                        "query": [{"key": "limit", "value": "5"}]},
                                             "header": [{"key": "Accept", "value": "application/json"}]},
                 "event": [{"listen": "test", "script": {"exec": ["pm.test('ok', function () { pm.response.to.have.status(200); });"]}}]},
                {"name": "Create", "request": {"method": "POST", "url": "{{base}}/users",
                                               "body": {"mode": "raw", "raw": "{\"a\": 1}", "options": {"raw": {"language": "json"}}},
                                               "auth": {"type": "basic", "basic": [{"key": "username", "value": "u"}, {"key": "password", "value": "p"}]}}},
            ]},
            {"name": "Form", "request": {"method": "POST", "url": "http://h/f",
                                         "body": {"mode": "formdata", "formdata": [{"key": "f", "type": "file", "src": "/tmp/x"}, {"key": "n", "value": "1", "disabled": True}]}}},
            {"name": "GQL", "request": {"method": "POST", "url": "http://h/g",
                                        "body": {"mode": "graphql", "graphql": {"query": "{ me }", "variables": "{\"a\":1}"}}}},
        ],
    }
    assert postman.is_collection(coll)
    conv = postman.from_collection(coll)
    assert conv["name"] == "Demo" and len(conv["folders"]) == 2 and len(conv["requests"]) == 4
    root = conv["folders"][0]
    assert root["auth"]["type"] == "bearer" and root["variables"][0]["key"] == "base"
    lst = conv["requests"][0]
    assert lst["url"] == "{{base}}/users" and lst["params"][0]["key"] == "limit" and lst["headers"][0]["key"] == "Accept"
    assert lst["tests"] == []
    assert lst["options"]["scripts"] == {"pre": "", "test": "pm.test('ok', function () { pm.response.to.have.status(200); });", "lang": "js"}
    create = conv["requests"][1]
    assert create["body_type"] == "json" and create["auth"]["type"] == "basic"
    form = conv["requests"][2]
    assert form["body_type"] == "multipart" and form["body"] == "f: @/tmp/x\n// n: 1"
    gql = conv["requests"][3]
    assert gql["body_type"] == "graphql" and json.loads(gql["body"])["variables"] == {"a": 1}

    back = postman.to_collection("Demo", conv["folders"], conv["requests"], {"g": "1"})
    assert back["info"]["schema"] == postman.SCHEMA
    assert back["item"][0]["name"] == "Demo" and back["item"][0]["auth"]["type"] == "bearer"
    users = back["item"][0]["item"][0]
    assert users["name"] == "Users" and users["item"][0]["request"]["url"]["query"][0]["key"] == "limit"
    assert "pm.response.to.have.status(200)" in users["item"][0]["event"][0]["script"]["exec"][0]
    assert users["item"][0]["event"][0]["listen"] == "test"
    assert users["item"][1]["request"]["body"]["mode"] == "raw"
    # python scripts export as comments, declarative tests as pm.test lines
    reqs = [{"name": "P", "method": "GET", "url": "http://h", "body_type": "none", "folder_id": None,
             "options": {"scripts": {"pre": "py.variables.set('a', 1)", "test": "", "lang": "py"}},
             "tests": [{"type": "status", "op": "equals", "value": "200", "enabled": True}]}]
    ev = postman.to_collection("X", [], reqs, {})["item"][0]["event"]
    assert ev[0]["listen"] == "prerequest" and ev[0]["script"]["exec"][1] == "// py.variables.set('a', 1)"
    assert ev[1]["listen"] == "test" and "pm.response.to.have.status(200)" in ev[1]["script"]["exec"][0]
    env = postman.from_environment({"name": "Dev", "values": [{"key": "a", "value": "1", "enabled": False}]})
    assert env["variables"] == [{"key": "a", "value": "1", "enabled": False}]
    assert postman.to_environment({"name": "Dev", "variables": env["variables"]})["values"][0]["enabled"] is False


def test_parse_data_file():
    rows = parse_data_file("email,name\na@x,A\nb@x,B\n", "users.csv")
    assert rows == [{"email": "a@x", "name": "A"}, {"email": "b@x", "name": "B"}]
    assert parse_data_file('[{"a": 1}, {"a": 2}]', "d.json") == [{"a": 1}, {"a": 2}]
    with pytest.raises(ValueError):
        parse_data_file('{"a": 1}', "d.json")


# ------------------------------------------------------------------ API tests
def test_request_roundtrip_new_fields(client):
    payload = {
        "name": "Login", "method": "POST", "url": "http://h/login", "body_type": "json",
        "bodies": [{"id": "b1", "name": "valid", "body": '{"u": 1} // c', "enabled": True}],
        "auth": {"type": "bearer", "token": "abc"},
        "tests": [{"type": "status", "op": "equals", "value": "200"}],
        "extract": [{"name": "token", "source": "json", "path": "data.token", "scope": "env"}],
        "pre_vars": [{"key": "ts", "value": "{{$timestamp}}", "scope": "local"}],
        "options": {"follow_redirects": False, "send_cookies": False},
    }
    r = client.post("/api/requests", json=payload)
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["auth"]["type"] == "bearer" and saved["auth"]["token"] == "abc" and saved["auth"]["prefix"] == "Bearer"
    assert saved["tests"][0]["op"] == "equals" and saved["tests"][0]["enabled"] is True
    assert saved["extract"][0]["scope"] == "env" and saved["pre_vars"][0]["key"] == "ts"
    assert saved["options"]["follow_redirects"] is False and saved["options"]["send_cookies"] is False
    assert saved["position"] == 1
    # default auth is "inherit", unknown body types fall back to text
    r2 = client.post("/api/requests", json={"name": "B", "body_type": "weird"}).json()
    assert r2["auth"]["type"] == "inherit" and r2["body_type"] == "text" and r2["position"] == 2
    # reorder
    client.post("/api/reorder", json={"request_ids": [r2["id"], saved["id"]]})
    assert [x["name"] for x in client.get("/api/requests").json()] == ["B", "Login"]


def test_send_strips_comments_and_applies_auth(client, echo_url):
    payload = {
        "name": "E", "method": "POST", "url": echo_url + "/echo", "body_type": "json",
        "bodies": [{"id": "b1", "name": "x", "body": '{\n  "a": 1, // comment\n  /* b */ "url": "http://x//y"\n}', "enabled": True}],
        "auth": {"type": "basic", "username": "bob", "password": "pw"},
        "headers": [{"key": "X-Custom", "value": "{{$randomInt 2 2}}", "enabled": True}],
        "tests": [{"type": "status", "op": "equals", "value": "200"}, {"type": "json", "target": "method", "op": "equals", "value": "POST"},
                  {"type": "json", "target": "headers.x-custom", "op": "equals", "value": "2"}],
    }
    r = client.post("/api/send", json=payload)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status_code"] == 200
    echoed = json.loads(res["response"]["body_text"])
    assert json.loads(echoed["body"]) == {"a": 1, "url": "http://x//y"}
    assert echoed["headers"]["authorization"] == "Basic " + base64.b64encode(b"bob:pw").decode()
    assert echoed["headers"]["content-type"] == "application/json"
    assert res["request"]["auth_type"] == "basic" and res["request"]["unresolved"] == []
    assert [t["passed"] for t in res["tests"]] == [True, True, True] and res["tests_failed"] == 0
    assert "body_base64" in res["response"] and res["response"]["is_binary"] is False
    # history recorded it
    h = client.get("/api/history").json()
    assert len(h) == 1 and h[0]["status_code"] == 200 and h[0]["request_name"] == "E"
    full = client.get(f"/api/history/{h[0]['id']}").json()
    assert full["snapshot"]["method"] == "POST" and full["detail"]["tests"][0]["passed"] is True
    assert client.delete("/api/history").json()["deleted"] == 1


def test_resolve_reports_unresolved_and_inherited_auth(client, echo_url):
    f = client.post("/api/folders", json={"name": "API"}).json()
    client.patch(f"/api/folders/{f['id']}", json={"auth": {"type": "apikey", "key": "X-Key", "value": "{{apikey}}", "add_to": "header"},
                                                   "headers": [{"key": "X-Folder", "value": "yes", "enabled": True}],
                                                   "variables": [{"key": "apikey", "value": "k1", "enabled": True}]})
    inh = client.get(f"/api/folders/{f['id']}/inherited").json()
    assert inh["auth"]["type"] == "apikey" and inh["headers"][0]["key"] == "X-Folder" and inh["variables"] == {"apikey": "k1"}
    r = client.post("/api/resolve", json={"name": "R", "url": "{{base}}/x/:id", "folder_id": f["id"],
                                           "params": [{"key": "id", "value": "7", "enabled": True}, {"key": "q", "value": "{{missing}}", "enabled": True}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["url"] == "{{base}}/x/7" and out["params"] == [["q", "{{missing}}"]]
    assert sorted(out["unresolved"]) == ["base", "missing"]
    assert ("X-Key", "k1") in [tuple(h) for h in out["headers"]] and ("X-Folder", "yes") in [tuple(h) for h in out["headers"]]
    assert out["auth_type"] == "apikey" and out["auth_from"] == "API"
    # request-level header overrides the folder header, request auth "none" blocks inheritance
    out = client.post("/api/resolve", json={"name": "R", "url": "http://h", "folder_id": f["id"], "auth": {"type": "none"},
                                             "headers": [{"key": "x-folder", "value": "no", "enabled": True}]}).json()
    assert [tuple(h) for h in out["headers"] if h[0] != "User-Agent"] == [("x-folder", "no")] and out["auth_type"] == "none"
    # sending inside the folder uses the folder variable and api key
    res = client.post("/api/send", json={"name": "R", "url": echo_url + "/echo", "folder_id": f["id"]}).json()
    assert json.loads(res["response"]["body_text"])["headers"]["x-key"] == "k1"


def test_cookies_redirects_and_binary(client, echo_url):
    res = client.post("/api/send", json={"name": "C", "url": echo_url + "/set-cookie"}).json()
    assert res["response"]["cookies"][0]["name"] == "session"
    jar = client.get("/api/cookies").json()
    assert jar[0]["name"] == "session" and jar[0]["value"] == "abc123" and jar[0]["domain"] == "127.0.0.1"
    res = client.post("/api/send", json={"name": "C2", "url": echo_url + "/echo"}).json()
    assert json.loads(res["response"]["body_text"])["cookies"] == {"session": "abc123"}
    res = client.post("/api/send", json={"name": "C3", "url": echo_url + "/echo", "options": {"send_cookies": False}}).json()
    assert json.loads(res["response"]["body_text"])["cookies"] == {}
    client.put("/api/cookies", json={"domain": "127.0.0.1", "name": "manual", "value": "v"})
    assert len(client.get("/api/cookies").json()) == 2
    assert client.delete("/api/cookies", params={"domain": "127.0.0.1", "name": "manual"}).json()["deleted"] == 1
    client.put("/api/settings", json={"send_cookies": False})
    res = client.post("/api/send", json={"name": "C4", "url": echo_url + "/echo"}).json()
    assert json.loads(res["response"]["body_text"])["cookies"] == {}
    client.put("/api/settings", json={"send_cookies": True})
    # redirects
    res = client.post("/api/send", json={"name": "R", "url": echo_url + "/redirect"}).json()
    assert res["status_code"] == 200 and res["response"]["hops"][0]["status_code"] == 302 and res["request"]["url"].endswith("from=redirect")
    res = client.post("/api/send", json={"name": "R", "url": echo_url + "/redirect", "options": {"follow_redirects": False}}).json()
    assert res["status_code"] == 302
    client.put("/api/settings", json={"follow_redirects": False})
    assert client.post("/api/send", json={"name": "R", "url": echo_url + "/redirect"}).json()["status_code"] == 302
    client.put("/api/settings", json={"follow_redirects": True})
    # binary
    res = client.post("/api/send", json={"name": "P", "url": echo_url + "/png"}).json()
    assert res["response"]["is_binary"] and res["response"]["body_base64"] and res["response"]["body_text"] == ""
    # digest
    res = client.post("/api/send", json={"name": "D", "url": echo_url + "/digest", "auth": {"type": "digest", "username": "u", "password": "p"}}).json()
    assert res["status_code"] == 200 and json.loads(res["response"]["body_text"])["auth"].startswith("Digest ")


def test_body_types(client, echo_url, tmp_path):
    f = tmp_path / "up.txt"
    f.write_text("hello")
    body = f"name: Ann\n// skipped: x\nfile: @{f}"
    res = client.post("/api/send", json={"name": "M", "method": "POST", "url": echo_url + "/echo", "body_type": "multipart",
                                          "bodies": [{"id": "b", "name": "b", "body": body, "enabled": True}]}).json()
    e = json.loads(res["response"]["body_text"])
    assert e["headers"]["content-type"].startswith("multipart/form-data") and "hello" in e["body"] and "Ann" in e["body"] and "skipped" not in e["body"]
    assert res["request"]["files"][0]["filename"] == "up.txt"
    res = client.post("/api/send", json={"name": "M2", "method": "POST", "url": echo_url + "/echo", "body_type": "multipart",
                                          "bodies": [{"id": "b", "name": "b", "body": "a: 1", "enabled": True}]}).json()
    e = json.loads(res["response"]["body_text"])
    assert e["headers"]["content-type"].startswith("multipart/form-data") and 'name="a"' in e["body"]
    res = client.post("/api/send", json={"name": "F", "method": "POST", "url": echo_url + "/echo", "body_type": "form",
                                          "bodies": [{"id": "b", "name": "b", "body": "a: 1\nb: x y", "enabled": True}]}).json()
    e = json.loads(res["response"]["body_text"])
    assert e["headers"]["content-type"] == "application/x-www-form-urlencoded" and e["body"] == "a=1&b=x+y"
    res = client.post("/api/send", json={"name": "B", "method": "POST", "url": echo_url + "/echo", "body_type": "binary",
                                          "bodies": [{"id": "b", "name": "b", "body": str(f), "enabled": True}]}).json()
    e = json.loads(res["response"]["body_text"])
    assert e["body"] == "hello" and e["headers"]["content-type"] == "text/plain"
    gql = json.dumps({"query": "{ me { id } }", "variables": "{\"a\": 1 // c\n}"})
    res = client.post("/api/send", json={"name": "G", "method": "POST", "url": echo_url + "/echo", "body_type": "graphql",
                                          "bodies": [{"id": "b", "name": "b", "body": gql, "enabled": True}]}).json()
    e = json.loads(res["response"]["body_text"])
    assert json.loads(e["body"]) == {"query": "{ me { id } }", "variables": {"a": 1}} and e["headers"]["content-type"] == "application/json"


def test_extract_sets_variables_and_runs_use_them(client, echo_url):
    env = client.post("/api/environments", json={"name": "Dev", "variables": []}).json()
    client.put("/api/environments/active", json={"id": env["id"]})
    a = client.post("/api/requests", json={
        "name": "A", "method": "POST", "url": echo_url + "/echo", "body_type": "json",
        "bodies": [{"id": "b", "name": "b", "body": '{"token": "secret-{{$randomInt 9 9}}"}', "enabled": True}],
        "extract": [{"name": "token", "source": "json", "path": "body", "scope": "env"},
                    {"name": "code", "source": "status", "path": "", "scope": "global"}],
        "pre_vars": [{"key": "who", "value": "me", "scope": "env"}],
    }).json()
    b = client.post("/api/requests", json={
        "name": "B", "url": echo_url + "/echo", "headers": [{"key": "X-Token", "value": "{{token}}", "enabled": True},
                                                            {"key": "X-Who", "value": "{{who}}", "enabled": True},
                                                            {"key": "X-Prev", "value": "{{prev.status}}", "enabled": True}],
        "tests": [{"type": "json", "target": "headers.x-token", "op": "contains", "value": "secret-9"},
                  {"type": "status", "op": "equals", "value": "999"}],
    }).json()
    run = client.post("/api/runs", json={"request_ids": [a["id"], b["id"]], "name": "chain"}).json()
    run = _wait_run(client, run["id"])
    assert run["status"] == "completed" and run["completed"] == 2
    assert run["failed"] == 1  # B fails its status test
    r2 = run["results"][1]
    assert [t["passed"] for t in r2["tests"]] == [True, False]
    e = json.loads(r2["detail"]["response"]["body_text"])
    assert e["headers"]["x-token"] == '{"token": "secret-9"}' and e["headers"]["x-who"] == "me" and e["headers"]["x-prev"] == "200"
    envs = client.get("/api/environments").json()["environments"]
    keys = {v["key"]: v["value"] for v in envs[0]["variables"]}
    assert keys["who"] == "me" and keys["token"].startswith("{")
    assert client.get("/api/variables").json()["code"] == "200"
    report = client.get(f"/api/runs/{run['id']}/report").json()
    assert report["tests_total"] == 2 and report["tests_failed"] == 1
    # fail_on_test off: nothing fails
    run = client.post("/api/runs", json={"request_ids": [b["id"]], "fail_on_test": False}).json()
    assert _wait_run(client, run["id"])["failed"] == 0


def test_iterations_and_data_rows(client, echo_url):
    a = client.post("/api/requests", json={"name": "A", "url": echo_url + "/echo",
                                           "params": [{"key": "email", "value": "{{email}}", "enabled": True},
                                                      {"key": "d", "value": "{{data.email}}", "enabled": True}]}).json()
    rows = client.post("/api/runs/data-file", json={"text": "email\na@x\nb@x\n", "filename": "u.csv"}).json()
    assert rows["columns"] == ["email"] and len(rows["rows"]) == 2
    run = client.post("/api/runs", json={"request_ids": [a["id"]], "data": rows["rows"]}).json()
    run = _wait_run(client, run["id"])
    assert run["total"] == 2 and run["status"] == "completed"
    urls = [r["detail"]["request"]["url"] for r in run["results"]]
    assert "email=a%40x" in urls[0] and "d=a%40x" in urls[0] and "email=b%40x" in urls[1]
    assert [r["iteration"] for r in run["results"]] == [1, 2]
    files = sorted(p.name for p in Path(run["output_dir"]).iterdir())
    assert any(n.startswith("i01_01_A") for n in files) and any(n.startswith("i02_01_A") for n in files)
    run = client.post("/api/runs", json={"request_ids": [a["id"]], "iterations": 3}).json()
    assert _wait_run(client, run["id"])["total"] == 3


def test_import_export_postman_and_curl(client):
    f = client.post("/api/folders", json={"name": "Shop"}).json()
    client.post("/api/requests", json={"name": "List", "url": "https://s/items", "folder_id": f["id"], "body_type": "json",
                                       "bodies": [{"id": "1", "name": "one", "body": "{}", "enabled": True},
                                                  {"id": "2", "name": "two", "body": "{\"a\":1}", "enabled": True}],
                                       "tests": [{"type": "status", "op": "equals", "value": "200"}]})
    out = client.get("/api/export/postman").json()
    coll = out["collection"]
    assert coll["info"]["schema"] == postman.SCHEMA
    shop = coll["item"][0]
    assert shop["name"] == "Shop" and [i["name"] for i in shop["item"]] == ["List · one", "List · two"]
    det = client.post("/api/import/detect", json={"data": coll}).json()
    assert det["kind"] == "postman_collection" and det["requests"] == 2
    res = client.post("/api/import/postman", json={"data": coll}).json()
    assert res["imported"] == 2 and res["folders"] == 2
    names = sorted(r["name"] for r in client.get("/api/requests").json())
    assert names == ["List", "List · one", "List · two"]
    res = client.post("/api/import/postman", json={"data": {"name": "QA", "values": [{"key": "a", "value": "1"}]}}).json()
    assert res["kind"] == "postman_environment"
    assert [e["name"] for e in client.get("/api/environments").json()["environments"]] == ["QA"]
    assert client.post("/api/import/detect", json={"data": client.get("/api/export").json()}).json()["kind"] == "pyhttp"
    # folder-only export turns the folder into the collection root
    sub = client.get("/api/export/postman", params={"folder_id": f["id"]}).json()["collection"]
    assert sub["info"]["name"] == "Shop" and [i["name"] for i in sub["item"]] == ["List · one", "List · two"]
    # curl import
    r = client.post("/api/import/curl", json={"command": "curl -X DELETE https://s/items/3 -H 'X: 1'", "folder_id": f["id"]})
    assert r.status_code == 201 and r.json()["method"] == "DELETE" and r.json()["folder_id"] == f["id"]
    assert client.post("/api/import/curl", json={"command": "curl", "save": False}).status_code == 400
    # export/import round-trip keeps folder settings and new request fields
    client.patch(f"/api/folders/{f['id']}", json={"auth": {"type": "bearer", "token": "t"}, "variables": [{"key": "v", "value": "1", "enabled": True}]})
    exp = client.get("/api/export").json()
    res = client.post("/api/import", json={**exp, "replace": True}).json()
    assert res["folders"] == len(exp["folders"])
    folders = client.get("/api/folders").json()
    shop = next(x for x in folders if x["name"] == "Shop")
    assert shop["auth"]["type"] == "bearer" and shop["variables"][0]["key"] == "v"
    lst = next(r for r in client.get("/api/requests").json() if r["name"] == "List")
    assert lst["tests"][0]["value"] == "200"


def test_javascript_scripts(client, echo_url):
    from pyhttp import jsengine
    assert jsengine.engine_name() in ("quickjs", "v8")
    env = client.post("/api/environments", json={"name": "Dev", "variables": [{"key": "base", "value": "b", "enabled": True}]}).json()
    client.put("/api/environments/active", json={"id": env["id"]})
    f = client.post("/api/folders", json={"name": "API"}).json()
    client.patch(f"/api/folders/{f['id']}", json={"variables": [{"key": "cv", "value": "c0", "enabled": True}]})
    pre = ('pm.request.headers.add({key: "X-Trace", value: pm.variables.get("base") + "-" + pm.collectionVariables.get("cv")});\n'
           'pm.request.url.query.add({key: "page", value: "2"});\n'
           'pm.variables.set("local_only", "L");\n'
           'pm.environment.set("pre_set", "yes");\n'
           'console.log("pre ran", pm.info.eventName);')
    test = ('pm.test("status is 200", function () { pm.response.to.have.status(200); pm.response.to.be.ok; });\n'
            'const body = pm.response.json();\n'
            'pm.test("header echoed", () => pm.expect(body.headers["x-trace"]).to.eql("b-c0"));\n'
            'pm.test("query echoed", () => pm.expect(body.query).to.have.property("page", "2"));\n'
            'pm.test("local var", () => pm.expect(pm.variables.get("local_only")).to.equal("L"));\n'
            'pm.test("fails", function () { pm.expect(pm.response.code).to.equal(201); });\n'
            'pm.environment.set("token", body.method);\n'
            'pm.globals.set("count", 2);\n'
            'pm.collectionVariables.set("cv", "c1");\n'
            'pm.environment.unset("base");\n'
            'tests["legacy"] = responseCode.code === 200;\n'
            'console.log("done", pm.response.responseTime > 0);')
    res = client.post("/api/send", json={"name": "J", "url": echo_url + "/echo", "folder_id": f["id"],
                                          "options": {"scripts": {"lang": "js", "pre": pre, "test": test}}}).json()
    assert res["status_code"] == 200, res
    assert [(t["name"], t["passed"]) for t in res["tests"]] == [
        ("status is 200", True), ("header echoed", True), ("query echoed", True), ("local var", True), ("fails", False), ("legacy", True)]
    assert "expected 200 to equal 201" in res["tests"][4]["note"]
    assert res["scripts"]["pre"]["logs"] == ["pre ran prerequest"] and res["scripts"]["test"]["logs"] == ["done true"]
    echoed = json.loads(res["response"]["body_text"])
    assert echoed["headers"]["x-trace"] == "b-c0" and echoed["query"] == {"page": "2"}
    envs = {v["key"]: v["value"] for v in client.get("/api/environments").json()["environments"][0]["variables"]}
    assert envs == {"pre_set": "yes", "token": "GET"}  # base was unset
    assert client.get("/api/variables").json()["count"] == "2"
    assert client.get(f"/api/folders/{f['id']}/inherited").json()["variables"] == {"cv": "c1"}
    names = [e["name"] for e in res["extracted"]]
    assert "token" in names and "base" in names
    # errors: syntax (line), runtime inside a test (note), runtime outside (fails the script with the line)
    res = client.post("/api/send", json={"name": "J", "url": echo_url + "/echo", "options": {"scripts": {"pre": "", "test": "pm.test('a', () => {});\nlet x = ;"}}}).json()
    assert res["tests"][-1]["name"] == "Test script" and "line 2" in res["tests"][-1]["note"] and "SyntaxError" in res["tests"][-1]["note"]
    res = client.post("/api/send", json={"name": "J", "url": echo_url + "/echo", "options": {"scripts": {"pre": "", "test": "pm.test('boom', () => { nope(); });\n\nfoo.bar;"}}}).json()
    notes = [(t["passed"], t["note"].replace("'", "")) for t in res["tests"]]  # QuickJS quotes the name, V8 does not
    assert notes == [(False, "nope is not defined"), (False, "ReferenceError: foo is not defined (line 3)")]
    res = client.post("/api/send", json={"name": "J", "url": echo_url + "/echo", "options": {"scripts": {"pre": "throw new Error('stop');", "test": ""}}}).json()
    assert res["status"] == "error" and "Pre-request script failed: Error: stop (line 1)" in res["error"]
    res = client.post("/api/send", json={"name": "J", "url": echo_url + "/echo", "options": {"scripts": {"pre": "", "test": "pm.sendRequest('http://x');"}}}).json()
    assert "pm.sendRequest is not supported" in res["tests"][-1]["note"] and "(line 1)" in res["tests"][-1]["note"]
    # timeout
    r = jsengine.run("while (true) {}", {"request": {}, "response": None, "variables": {}}, event="test", timeout=0.3)
    assert r["timed_out"] and not r["ok"]
    # imported Postman scripts run as-is
    coll = {"info": {"name": "Imp", "schema": postman.SCHEMA}, "item": [{"name": "E", "request": {"method": "GET", "url": echo_url + "/echo"},
            "event": [{"listen": "test", "script": {"exec": ["pm.test('imported', function () { pm.response.to.have.status(200); });"]}}]}]}
    client.post("/api/import/postman", json={"data": coll})
    rid = next(r["id"] for r in client.get("/api/requests").json() if r["name"] == "E")
    run = client.post("/api/runs", json={"request_ids": [rid]}).json()
    run = _wait_run(client, run["id"])
    assert run["failed"] == 0 and run["results"][0]["tests"] == [{"name": "imported", "passed": True, "note": ""}]


def test_python_scripts(client, echo_url):
    env = client.post("/api/environments", json={"name": "Dev", "variables": []}).json()
    client.put("/api/environments/active", json={"id": env["id"]})
    pre = 'py.request.headers["X-Script"] = "yes"\npy.request.params["p"] = py.variables.get("missing", "dflt")\npy.variables.set("local_only", "L")\nprint("pre ran")'
    test = ('r = py.response\npy.test("status 200", r.status_code == 200)\n'
            'pm.test("echoed header", lambda: pm.expect(r.json()["headers"]["x-script"]).to_equal("yes"))\n'
            'py.test("query", lambda: py.expect(r.json()["query"]).to_have_key("p"))\n'
            'py.test("fails", lambda: py.expect(r.status_code).to_equal(999))\n'
            'py.environment.set("from_script", r.json()["method"])\nprint("len", r.size)')
    res = client.post("/api/send", json={"name": "S", "url": echo_url + "/echo", "options": {"scripts": {"lang": "py", "pre": pre, "test": test}}}).json()
    assert res["status_code"] == 200
    assert [(t["name"], t["passed"]) for t in res["tests"]] == [("status 200", True), ("echoed header", True), ("query", True), ("fails", False)]
    assert "expected 200 to equal 999" in res["tests"][3]["note"]
    assert res["scripts"]["pre"]["ok"] and res["scripts"]["pre"]["logs"] == ["pre ran"]
    assert res["scripts"]["test"]["logs"][0].startswith("len ")
    echoed = json.loads(res["response"]["body_text"])
    assert echoed["headers"]["x-script"] == "yes" and echoed["query"] == {"p": "dflt"}
    envs = client.get("/api/environments").json()["environments"]
    assert {v["key"]: v["value"] for v in envs[0]["variables"]} == {"from_script": "GET"}
    # a broken pre-request script fails the request with the error; a broken test script fails one test
    res = client.post("/api/send", json={"name": "S", "url": echo_url + "/echo", "options": {"scripts": {"lang": "py", "pre": "x = 1 /", "test": ""}}}).json()
    assert res["status"] == "error" and "Pre-request script failed" in res["error"] and "line 1" in res["error"]
    # JavaScript habits work: const/let/var, trailing ;, dotted JSON access, true/false/null, console.log
    js = ('const method = pm.response.json().method;\n'
          'let missing = py.response.json().nope;\n'
          'var hdrs = py.response.json().headers;\n'
          'py.environment.set("js_method", method);\n'
          'py.test("dotted access", method == "GET" and missing == null and hdrs.host != null);\n'
          'py.test("length", py.response.json().headers.length > 1 and true);\n'
          'console.log("logged", false);')
    res = client.post("/api/send", json={"name": "S", "url": echo_url + "/echo", "options": {"scripts": {"lang": "py", "pre": "", "test": js}}}).json()
    assert [(t["name"], t["passed"], t["note"]) for t in res["tests"]] == [("dotted access", True, ""), ("length", True, "")]
    assert res["scripts"]["test"]["logs"] == ["logged False"]
    envs = client.get("/api/environments").json()["environments"]
    assert {v["key"]: v["value"] for v in envs[0]["variables"]}["js_method"] == "GET"
    # real JavaScript still fails with a helpful message naming the line
    js = 'py.test("x", function () { return 1; });'
    res = client.post("/api/send", json={"name": "S", "url": echo_url + "/echo", "options": {"scripts": {"lang": "py", "pre": "", "test": js}}}).json()
    note = res["tests"][-1]["note"]
    assert res["tests"][-1]["passed"] is False and "line 1" in note and "scripts are Python" in note
    # runtime errors name the line too
    res = client.post("/api/send", json={"name": "S", "url": echo_url + "/echo", "options": {"scripts": {"lang": "py", "pre": "", "test": "x = 1\ny = py.response.json()['nope']"}}}).json()
    assert "KeyError" in res["tests"][-1]["note"] and "(line 2)" in res["tests"][-1]["note"]
    res = client.post("/api/send", json={"name": "S", "url": echo_url + "/echo", "options": {"scripts": {"lang": "py", "pre": "", "test": "pm.test('ok', True)\nraise ValueError('boom')"}}}).json()
    assert [(t["name"], t["passed"]) for t in res["tests"]] == [("ok", True), ("Test script", False)]
    assert "boom" in res["tests"][1]["note"]
    # scripts survive save / load
    saved = client.post("/api/requests", json={"name": "S", "options": {"scripts": {"pre": pre, "test": test}}}).json()
    assert saved["options"]["scripts"]["pre"] == pre


def test_cancel_send(client, echo_url):
    import threading
    out: dict = {}
    t0 = time.time()

    def go() -> None:
        out["res"] = client.post("/api/send", json={"name": "Slow", "url": echo_url + "/slow?seconds=8", "send_id": "abc"}).json()

    th = threading.Thread(target=go)
    th.start()
    time.sleep(0.6)
    assert client.post("/api/send/abc/cancel").json() == {"cancelled": True}
    th.join(10)
    assert out["res"]["status"] == "cancelled" and out["res"]["error"] == "Cancelled by user"
    assert time.time() - t0 < 6, "cancel did not interrupt the call"
    assert client.post("/api/send/abc/cancel").json() == {"cancelled": False}
    assert client.get("/api/history").json()[0]["status"] == "cancelled"


def test_cancel_before_send_starts(client, echo_url):
    """Cancel pressed before the send reached the server (a busy machine) still cancels it."""
    assert client.post("/api/send/early1/cancel").json() == {"cancelled": True}
    t0 = time.time()
    res = client.post("/api/send", json={"name": "Slow", "url": echo_url + "/slow?seconds=8", "send_id": "early1"}).json()
    assert res["status"] == "cancelled" and res["error"] == "Cancelled by user"
    assert time.time() - t0 < 3
    # the early cancel is used up: a later send with the same id runs normally
    res = client.post("/api/send", json={"name": "Fast", "url": echo_url + "/echo", "send_id": "early1"}).json()
    assert res["status"] == "ok"


def test_font_settings(client):
    s = client.get("/api/settings").json()
    assert s["ui_font"] == "inter" and s["mono_font"] == "default"
    s = client.put("/api/settings", json={"ui_font": "segoe", "mono_font": "jetbrains"}).json()
    assert s["ui_font"] == "segoe" and s["mono_font"] == "jetbrains"
    assert client.put("/api/settings", json={"ui_font": "comic"}).status_code == 400
    assert client.put("/api/settings", json={"mono_font": "comic"}).status_code == 400
    client.put("/api/settings", json={"ui_font": "inter", "mono_font": "default"})


def test_settings_roundtrip(client):
    s = client.get("/api/settings").json()
    assert s["follow_redirects"] is True and s["max_redirects"] == 10 and s["certs"] == [] and s["layout"] == "below"
    s = client.put("/api/settings", json={"max_redirects": 3, "proxy_url": "http://p:1", "certs": [{"host": "a", "cert": "c.pem"}],
                                           "editor_font_size": 15, "layout": "beside", "user_agent": "me/1"}).json()
    assert s["max_redirects"] == 3 and s["proxy_url"] == "http://p:1" and s["certs"][0]["host"] == "a" and s["editor_font_size"] == 15
    assert client.put("/api/settings", json={"layout": "sideways"}).status_code == 400
    assert client.get("/api/update-check").json()["available"] is False


def test_legacy_behaviour_unchanged(client, echo_url):
    """The original API surface still works the same way."""
    client.delete("/api/console")  # the console is process-wide, not per data directory
    r = client.post("/api/requests", json={"name": "Old", "url": echo_url + "/echo", "tags": ["a"]}).json()
    assert r["name"] == "Old" and r["tags"] == ["a"] and r["body_type"] == "none"
    assert client.get("/api/tags").json() == [{"tag": "a", "count": 1}]
    d = client.post(f"/api/requests/{r['id']}/duplicate").json()
    assert d["name"] == "Old (copy)"
    run = client.post("/api/runs", json={"request_ids": [r["id"], d["id"]], "name": "old run"}).json()
    run = _wait_run(client, run["id"])
    assert run["status"] == "completed" and run["completed"] == 2 and run["failed"] == 0
    assert (Path(run["output_dir"]) / "summary.json").is_file()
    assert client.get("/api/runs").json()[0]["name"] == "old run"
    assert client.delete(f"/api/requests/{d['id']}").json() == {"ok": True}
    assert client.get(f"/api/requests/{d['id']}").status_code == 404
    con = client.get("/api/console").json()
    assert con["count"] == 2 and con["entries"][0]["source"].startswith("Run:")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ------------------------------------------------------------------ in-app updates
def test_updater_helpers(tmp_path):
    import hashlib
    from pyhttp import updater

    assets = [{"name": "EULA.md", "url": "e"}, {"name": "pyhttp-0.2.0-windows-x64-setup.exe", "url": "s"},
              {"name": "pyhttp-0.2.0-windows-x64.exe", "url": "p"}, {"name": "pyhttp-0.2.0-macos-arm64.dmg", "url": "m"},
              {"name": "SHA256SUMS.txt", "url": "h"}]
    assert updater.pick_asset(assets, "installer", "win32")["url"] == "s"
    assert updater.pick_asset(assets, "portable", "win32")["url"] == "p"
    assert updater.pick_asset(assets, "source", "win32")["url"] == "p"
    assert updater.pick_asset([a for a in assets if a["url"] != "p"], "portable", "win32")["url"] == "s"   # only an installer published
    assert updater.pick_asset([a for a in assets if a["url"] != "s"], "installer", "win32")["url"] == "p"  # older release: no installer
    assert updater.pick_asset(assets, "brew", "darwin", "arm64")["url"] == "m"
    assert updater.pick_asset(assets, "app", "darwin", "x86_64")["url"] == "m"   # the only dmg there is
    assert updater.pick_asset(assets, "other", "linux") is None
    assets += [{"name": "pyhttp-0.2.0-linux-arm64.deb", "url": "da"}, {"name": "pyhttp-0.2.0-linux-amd64.deb", "url": "dx"}]
    assert updater.pick_asset(assets, "deb", "linux", "x86_64")["url"] == "dx"
    assert updater.pick_asset(assets, "deb", "linux", "aarch64")["url"] == "da"
    assert updater.pick_asset(assets[:-1], "other", "linux", "x86_64")["url"] == "da"   # the only .deb there is
    title, note, cmds = updater._commands("deb", "0.2.0")
    assert cmds == [f"sudo apt install ./pyhttp-0.2.0-linux-{updater.deb_arch()}.deb"] and "Download" in note

    h1, h2 = "a" * 64, "B" * 64
    sums = updater.parse_sha256sums(f"{h1}  pyhttp-0.2.0-windows-x64.exe\n{h2} *artifacts/pyhttp-0.2.0-windows-x64-setup.exe\nnot a checksum line\n")
    assert sums == {"pyhttp-0.2.0-windows-x64.exe": h1, "pyhttp-0.2.0-windows-x64-setup.exe": h2.lower()}
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert updater.sha256_of(f) == hashlib.sha256(b"hello").hexdigest()

    new, exe, log = tmp_path / "dl" / "new.exe", tmp_path / "app" / "pyhttp.exe", tmp_path / "dl" / "setup.log"
    script = updater.windows_script("portable", new, exe, [11, 22], log)
    assert "Wait-Process -Id 11,22" in script and f'copy /y "{new}" "{exe}"' in script and f'start "" "{exe}"' in script
    assert f'"{exe}.old"' in script and "\r\n" in script
    script = updater.windows_script("installer", new, exe, [11], log)
    assert f'"{new}" /VERYSILENT' in script and f'/LOG="{log}"' in script and "move /y" not in script

    info = updater.info()   # the tests run from source
    assert info["kind"] == "source" and info["self_update"] is False and "git pull" in info["commands"]
    with pytest.raises(RuntimeError):
        updater.Updater(tmp_path).start({"available": True, "latest": "9.9.9", "assets": assets})


def test_updater_windows_flow(tmp_path, echo_url, monkeypatch):
    """Download, verify against SHA256SUMS.txt, write the helper script and hand over (Popen is faked)."""
    from pyhttp import updater

    launched: list[list[str]] = []
    monkeypatch.setattr(updater.sys, "platform", "win32")   # the Windows flow, also when the tests run on macOS/Linux CI
    monkeypatch.setattr(updater, "install_kind", lambda: "portable")
    monkeypatch.setattr(updater, "_quit_later", lambda *a, **k: None)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd))
    assets = [{"name": "pyhttp-9.9.9-windows-x64.exe", "url": f"{echo_url}/release/pyhttp-9.9.9-windows-x64.exe", "size": len(FAKE_EXE)},
              {"name": "SHA256SUMS.txt", "url": f"{echo_url}/release/SHA256SUMS.txt", "size": 1}]
    u = updater.Updater(tmp_path)
    assert u.start({"available": True, "latest": "9.9.9", "assets": assets})["state"] in ("starting", "downloading")
    with pytest.raises(RuntimeError, match="already running"):
        u.start({"available": True, "latest": "9.9.9", "assets": assets})
    u._thread.join(30)
    st = u.status()
    assert st["state"] == "restarting", st
    assert (tmp_path / "updates" / "pyhttp-9.9.9-windows-x64.exe").read_bytes() == FAKE_EXE
    script = (tmp_path / "updates" / "apply-update.cmd").read_text(encoding="utf-8")
    assert "Wait-Process" in script and str(Path(sys.executable).resolve()) in script   # resolved: venvs symlink python
    assert launched and launched[0][:2] == ["cmd.exe", "/c"] and launched[0][2].endswith("apply-update.cmd")

    # a damaged download is refused (the installer's checksum in the file is wrong on purpose)
    bad = [{"name": "pyhttp-9.9.9-windows-x64-setup.exe", "url": f"{echo_url}/release/pyhttp-9.9.9-windows-x64.exe", "size": 1}, assets[1]]
    monkeypatch.setattr(updater, "install_kind", lambda: "installer")
    u = updater.Updater(tmp_path)
    u.start({"available": True, "latest": "9.9.9", "assets": bad})
    u._thread.join(30)
    assert u.status()["state"] == "error" and "checksum" in u.status()["error"]
    assert not (tmp_path / "updates" / "pyhttp-9.9.9-windows-x64-setup.exe").exists()

    # no checksum file at all: refused before anything is downloaded
    with pytest.raises(RuntimeError, match="SHA256SUMS"):
        updater.Updater(tmp_path).start({"available": True, "latest": "9.9.9", "assets": assets[:1]})


def test_update_endpoints(client):
    m = client.get("/api/meta").json()
    assert m["install"]["kind"] == "source" and m["install"]["self_update"] is False
    assert client.get("/api/update/status").json()["state"] == "idle"
    r = client.post("/api/update/install")
    assert r.status_code == 400 and "No update" in r.json()["detail"]


# ------------------------------------------------------------------ dependent requests (token first)
def test_dependent_request(client, echo_url):
    # "Get token" echoes a query value and saves it as {{token}} in the environment
    env = client.post("/api/environments", json={"name": "dev", "variables": []}).json()
    client.put("/api/environments/active", json={"id": env["id"]})
    token = client.post("/api/requests", json={"name": "Get token", "url": echo_url + "/echo?t=abc",
                                               "extract": [{"name": "token", "scope": "env", "source": "json", "path": "query.t", "enabled": True}]}).json()
    main = {"name": "List users", "url": echo_url + "/echo", "headers": [{"key": "Authorization", "value": "Bearer {{token}}", "enabled": True}],
            "options": {"depends": {"request_id": token["id"], "ttl": 600, "retry_on_auth": True}}}
    saved = client.post("/api/requests", json=main).json()
    assert saved["options"]["depends"] == {"request_id": token["id"], "ttl": 600, "retry_on_auth": True}

    # first send: the token request runs first and the header carries its value
    r = client.post("/api/send", json={**main, "id": saved["id"]}).json()
    assert r["status_code"] == 200 and r["dependency"]["state"] == "ran" and r["dependency"]["ok"]
    assert json.loads(r["response"]["body_text"])["headers"]["authorization"] == "Bearer abc"
    status = client.get(f"/api/requests/{token['id']}/dependency").json()
    assert status["ok"] == 1 and status["age"] < 5
    # second send within the expiry: reused, not sent again
    r = client.post("/api/send", json={**main, "id": saved["id"]}).json()
    assert r["dependency"]["state"] == "fresh" and r["dependency"]["ttl"] == 600
    history = [h for h in client.get("/api/history").json() if h["request_name"] == "Get token"]
    assert len(history) == 1 and history[0]["source"] == "Auto: before List users"
    # expiry 0: before every send
    every = {**main, "options": {"depends": {"request_id": token["id"], "ttl": 0}}}
    assert client.post("/api/send", json=every).json()["dependency"]["state"] == "ran"
    # reset: sent again before the next send even though it is fresh
    client.delete(f"/api/requests/{token['id']}/dependency")
    assert client.post("/api/send", json={**main, "id": saved["id"]}).json()["dependency"]["state"] == "ran"

    # a 401 with a fresh token: the token is refreshed once and the request retried
    unauth = {**main, "url": echo_url + "/status/401"}
    before = len([h for h in client.get("/api/history").json() if h["request_name"] == "Get token"])
    r = client.post("/api/send", json=unauth).json()
    assert r["status_code"] == 401 and r["dependency"]["retried"] is True and r["dependency"]["state"] == "ran"
    after = len([h for h in client.get("/api/history").json() if h["request_name"] == "Get token"])
    assert after == before + 1
    # ... but not when retry is off
    r = client.post("/api/send", json={**unauth, "options": {"depends": {"request_id": token["id"], "ttl": 600, "retry_on_auth": False}}}).json()
    assert r["status_code"] == 401 and r["dependency"]["state"] == "fresh" and "retried" not in r["dependency"]

    # a failing dependency stops the send with a clear error
    bad = client.post("/api/requests", json={"name": "Bad token", "url": echo_url + "/status/500"}).json()
    r = client.post("/api/send", json={**main, "options": {"depends": {"request_id": bad["id"], "ttl": 600}}}).json()
    assert r["status"] == "error" and "Bad token" in r["error"] and "HTTP 500" in r["error"] and r["dependency"]["ok"] is False
    # a loop (A depends on B, B depends on A) does not recurse
    client.put(f"/api/requests/{token['id']}", json={**token, "options": {"depends": {"request_id": saved["id"], "ttl": 0}}})
    r = client.post("/api/send", json={**main, "id": saved["id"], "options": {"depends": {"request_id": token["id"], "ttl": 0}}}).json()
    assert r["status_code"] == 200 and r["dependency"]["state"] == "ran"
    # a deleted dependency is reported, not crashed on
    client.delete(f"/api/requests/{bad['id']}")
    r = client.post("/api/send", json={**main, "options": {"depends": {"request_id": bad["id"], "ttl": 600}}}).json()
    assert r["status"] == "error" and "no longer exists" in r["error"]

    # parallel sends share one token fetch
    bodies = [{"id": f"b{i}", "name": f"b{i}", "body": "{}", "enabled": True} for i in range(4)]
    client.put("/api/settings", json={"parallel": True, "concurrency": 8})
    client.delete(f"/api/requests/{token['id']}/dependency")
    before = len([h for h in client.get("/api/history").json() if h["request_name"] == "Get token"])
    run = client.post("/api/send-bodies", json={**main, "id": saved["id"], "method": "POST", "body_type": "json", "bodies": bodies}).json()
    run = _wait_run(client, run["id"])
    assert run["completed"] == 4 and run["failed"] == 0
    after = len([h for h in client.get("/api/history").json() if h["request_name"] == "Get token"])
    assert after == before + 1


# ------------------------------------------------------------------ sample data file for the runner
def test_sample_data_file(client, echo_url):
    from pyhttp.app import parse_data_file

    client.put("/api/variables", json={"variables": {"base": echo_url}})
    a = client.post("/api/requests", json={"name": "Create", "method": "POST", "url": "{{base}}/users?page={{page}}", "body_type": "json",
                                           "headers": [{"key": "X-Trace", "value": "{{$guid}}", "enabled": True}],
                                           "bodies": [{"id": "b", "name": "b", "body": '{"email": "{{email}}", "name": "{{name}}", "n": "{{data.n}}", "id": "{{user_id}}"}', "enabled": True}]}).json()
    b = client.post("/api/requests", json={"name": "Plain", "url": echo_url + "/echo"}).json()

    f = client.get(f"/api/runs/sample-data?request_ids={a['id']},{b['id']}").json()
    # one column per variable the requests use and have no value for; dynamic, data.* and defined ones are left out
    assert f["columns"] == ["page", "email", "name", "user_id"] and f["filename"] == "sample-variables.csv"
    rows = parse_data_file(f["text"], f["filename"])
    assert len(rows) == 3 and rows[0] == {"page": "1", "email": "alice@example.com", "name": "Alice", "user_id": "1"}
    assert client.post("/api/runs/data-file", json={"text": f["text"], "filename": f["filename"]}).json()["rows"] == rows
    j = client.get(f"/api/runs/sample-data?request_ids={a['id']}&format=json").json()
    assert j["filename"] == "sample-variables.json" and json.loads(j["text"]) == rows
    # requests without variables still get a usable sample
    g = client.get(f"/api/runs/sample-data?request_ids={b['id']}").json()
    assert g["columns"] == ["name", "email", "user_id"] and len(parse_data_file(g["text"])) == 3
    assert client.get("/api/runs/sample-data").json()["columns"] == ["name", "email", "user_id"]

    # the sample drives a run: one iteration per row, the columns resolve in the request
    run = client.post("/api/runs", json={"request_ids": [a["id"]], "data": rows, "name": "sample"}).json()
    run = _wait_run(client, run["id"])
    assert run["status"] == "completed" and run["total"] == 3 and [r["iteration"] for r in run["results"]] == [1, 2, 3]
    sent = [json.loads(r["detail"]["request"]["body"])["email"] for r in run["results"]]
    assert sent == ["alice@example.com", "bob@example.com", "carol@example.com"]


# ------------------------------------------------------------------ scripts scoped to one body
def test_scripts_scoped_to_body(client, echo_url):
    from pyhttp import runner

    bodies = [{"id": "b1", "name": "valid", "body": '{"email": "a@b.c"}', "enabled": True},
              {"id": "b2", "name": "missing email", "body": '{"name": "x"}', "enabled": False}]
    scripts = {"lang": "js",
               "pre": 'pm.request.headers.add({ key: "X-All", value: "yes" });\nconsole.log("all pre");',
               "test": 'pm.test("all bodies", () => pm.response.to.have.status(200));',
               "bodies": {"b1": {"pre": 'pm.request.headers.add({ key: "X-Own", value: pm.variables.get("body_name") });\nconsole.log("own pre");', "test": ""},
                          "b2": {"pre": "", "test": 'pm.test("only missing", () => pm.expect(pm.response.json().headers["x-all"]).to.equal("yes"));'},
                          "gone": {"pre": 'throw new Error("deleted body script must not run");', "test": ""}}}
    req = {"name": "Scoped scripts", "method": "POST", "url": echo_url + "/echo", "body_type": "json", "bodies": bodies,
           "options": {"scripts": scripts}}
    items = runner.expand({**req, "bodies": [{**b, "enabled": True} for b in bodies]})
    assert runner.scripts_for_body(items[0])["pre"].startswith("pm.request.headers.add") and runner.scripts_for_body(items[0])["test"] == ""
    assert runner.scripts_for_body(items[1])["pre"] == "" and "only missing" in runner.scripts_for_body(items[1])["test"]
    assert runner.scripts_for_body({**items[0], "body_id": "gone"}) == {"pre": "", "test": ""}

    # saved requests keep per-body scripts (empty ones are dropped)
    saved = client.post("/api/requests", json={**req, "options": {"scripts": {**scripts, "bodies": {**scripts["bodies"], "b3": {"pre": " ", "test": ""}}}}}).json()
    assert set(saved["options"]["scripts"]["bodies"]) == {"b1", "b2", "gone"}

    r = client.post("/api/send", json=req).json()   # sends "valid": all-bodies scripts plus b1's own pre-request
    assert r["status_code"] == 200, (r["error"], r.get("scripts"))
    echoed = json.loads(r["response"]["body_text"])
    assert echoed["headers"]["x-all"] == "yes" and echoed["headers"]["x-own"] == "valid"
    assert [t["name"] for t in r["tests"]] == ["all bodies"] and r["tests_failed"] == 0
    assert r["scripts"]["pre"]["logs"] == ["all pre"] and r["scripts"]["pre_body"]["logs"] == ["own pre"]
    assert r["scripts"]["pre_body"]["body_name"] == "valid" and "test_body" not in r["scripts"]

    bodies[0]["enabled"], bodies[1]["enabled"] = False, True
    r = client.post("/api/send", json=req).json()   # sends "missing email": its own test script runs after the shared one
    echoed = json.loads(r["response"]["body_text"])
    assert echoed["headers"]["x-all"] == "yes" and "x-own" not in echoed["headers"]
    assert [t["name"] for t in r["tests"]] == ["all bodies", "only missing"] and r["tests_failed"] == 0
    assert "pre_body" not in r["scripts"] and r["scripts"]["test_body"]["body_name"] == "missing email"

    # a failing body-specific pre-request script stops the send like the shared one does
    bad = {**req, "options": {"scripts": {**scripts, "bodies": {"b2": {"pre": "throw new Error('nope');", "test": ""}}}}}
    r = client.post("/api/send", json=bad).json()
    assert r["status"] == "error" and "nope" in r["error"] and r["scripts"]["pre"]["ok"] and not r["scripts"]["pre_body"]["ok"]

    # Python scripts chain the same way
    py = {"lang": "py", "pre": 'py.request.headers["X-All"] = "yes"', "test": 'py.test("all", py.response.status_code == 200)',
          "bodies": {"b2": {"pre": 'py.request.headers["X-Own"] = py.variables.get("body_name")', "test": 'py.test("own", py.response.json()["headers"]["x-own"] == "missing email")'}}}
    r = client.post("/api/send", json={**req, "options": {"scripts": py}}).json()
    assert [(t["name"], t["passed"]) for t in r["tests"]] == [("all", True), ("own", True)], r["tests"]


# ------------------------------------------------------------------ tests scoped to one body
def test_tests_scoped_to_body(client, echo_url):
    from pyhttp import runner

    bodies = [{"id": "b1", "name": "valid", "body": '{"email": "a@b.c"}', "enabled": True},
              {"id": "b2", "name": "missing email", "body": '{"name": "x"}', "enabled": False}]
    tests = [{"type": "status", "op": "equals", "value": "200", "name": "all bodies"},
             {"type": "status", "op": "equals", "value": "200", "name": "only valid", "body_id": "b1"},
             {"type": "status", "op": "equals", "value": "200", "name": "only missing", "body_id": "b2"},
             {"type": "status", "op": "equals", "value": "200", "name": "deleted body scope", "body_id": "gone"}]
    req = {"name": "Scoped", "method": "POST", "url": echo_url + "/echo", "body_type": "json", "bodies": bodies, "tests": tests,
           "headers": [{"key": "X-Body", "value": "{{body_name}}", "enabled": True}]}
    items = runner.expand(req)
    assert [i["body_id"] for i in items] == ["b1"]
    assert [t["name"] for t in runner.tests_for_body(items[0])] == ["all bodies", "only valid", "deleted body scope"]
    both = runner.expand({**req, "bodies": [{**b, "enabled": True} for b in bodies]})
    assert [i["body_id"] for i in both] == ["b1", "b2"]
    assert [t["name"] for t in runner.tests_for_body(both[1])] == ["all bodies", "only missing", "deleted body scope"]

    r = client.post("/api/send", json=req).json()
    assert [t["name"] for t in r["tests"]] == ["all bodies", "only valid", "deleted body scope"] and r["tests_failed"] == 0
    assert json.loads(r["response"]["body_text"])["headers"]["x-body"] == "valid"   # {{body_name}} resolves per body
    bodies[0]["enabled"], bodies[1]["enabled"] = False, True
    r = client.post("/api/send", json=req).json()
    assert [t["name"] for t in r["tests"]] == ["all bodies", "only missing", "deleted body scope"]
    assert json.loads(r["response"]["body_text"])["headers"]["x-body"] == "missing email"
    saved = client.post("/api/requests", json=req).json()
    assert saved["tests"][1]["body_id"] == "b1"   # the scope round-trips through the database


def test_parallel_sends_start_together(client, echo_url):
    """Parallel bodies all start at once: per-request setup (the SSL context) must not block the
    event loop, which used to make parallel sends go out about 130 ms apart, one after another."""
    from pyhttp import runner
    runner._ssl_cache.clear()
    client.put("/api/settings", json={"parallel": True, "concurrency": 8})
    try:
        bodies = [{"id": f"p{i}", "name": f"p{i}", "body": "{}", "enabled": True} for i in range(6)]
        req = {"name": "Parallel", "method": "GET", "url": echo_url + "/slow?seconds=1", "body_type": "json", "bodies": bodies}
        saved = client.post("/api/requests", json=req).json()
        t0 = time.time()
        run = client.post("/api/send-bodies", json={**req, "id": saved["id"]}).json()
        assert run["options"]["parallel"] is True
        run = _wait_run(client, run["id"])
        took = time.time() - t0
        assert run["completed"] == 6 and run["failed"] == 0
        starts = [r["started_at"] for r in run["results"]]
        assert max(starts) - min(starts) < 0.3, f"sends were staggered: {[round(s - min(starts), 2) for s in starts]}"
        assert took < 3, f"6 parallel sends of 1 s took {took:.1f} s"
    finally:
        client.put("/api/settings", json={"parallel": False})


def test_parallel_bodies_share_a_dependency_fetch(client, echo_url):
    """Send ×N in parallel with a dependency sent every time (TTL 0): it is fetched once and shared,
    not once per body in a row (which made the parallel send take N × the token time)."""
    client.put("/api/settings", json={"parallel": True, "concurrency": 8})
    try:
        token = client.post("/api/requests", json={"name": "Slow token", "method": "GET", "url": echo_url + "/slow?seconds=1"}).json()
        bodies = [{"id": f"d{i}", "name": f"d{i}", "body": "{}", "enabled": True} for i in range(4)]
        req = {"name": "Needs token", "method": "GET", "url": echo_url + "/slow?seconds=1", "body_type": "json", "bodies": bodies,
               "options": {"depends": {"request_id": token["id"], "ttl": 0}}}
        saved = client.post("/api/requests", json=req).json()
        t0 = time.time()
        run = _wait_run(client, client.post("/api/send-bodies", json={**req, "id": saved["id"]}).json()["id"])
        assert run["completed"] == 4 and run["failed"] == 0
        assert time.time() - t0 < 3.5, "the bodies waited for one token fetch each"
        assert len([h for h in client.get("/api/history").json() if h["request_name"] == "Slow token"]) == 1
        states = sorted(r["detail"]["dependency"]["state"] for r in run["results"])
        assert states == ["ran", "shared", "shared", "shared"]
        # a later send with TTL 0 fetches it again
        client.post("/api/send", json={**req, "body_type": "none"})
        assert len([h for h in client.get("/api/history").json() if h["request_name"] == "Slow token"]) == 2
    finally:
        client.put("/api/settings", json={"parallel": False})


# ------------------------------------------------------------------ monitors
def test_monitors_crud_and_check(client, echo_url):
    r1 = client.post("/api/requests", json={"name": "Mon ok", "url": echo_url + "/echo"}).json()
    r2 = client.post("/api/requests", json={"name": "Mon 404", "url": echo_url + "/nope"}).json()
    assert client.post("/api/monitors", json={"name": "x", "request_ids": []}).status_code == 400
    assert client.post("/api/monitors", json={"name": "x", "request_ids": ["bogus"]}).status_code == 400
    assert client.post("/api/monitors", json={"name": "x", "request_ids": [r1["id"]], "interval_s": 3}).status_code == 400
    mon = client.post("/api/monitors", json={"name": "Health", "request_ids": [r1["id"]], "interval_s": 60}).json()
    assert mon["interval_s"] == 60 and mon["enabled"]

    # run now: a passing check
    chk = client.post(f"/api/monitors/{mon['id']}/run-now").json()
    assert chk["ok"] and chk["failed"] == 0 and chk["total"] == 1
    ls = client.get("/api/monitors").json()
    assert ls["failing"] == 0 and ls["monitors"][0]["last_status"] == "pass"
    assert len(ls["monitors"][0]["recent"]) == 1 and "results" not in ls["monitors"][0]["recent"][0]

    # a 404 in the set flips the monitor to failing
    client.patch(f"/api/monitors/{mon['id']}", json={"request_ids": [r1["id"], r2["id"]]})
    chk = client.post(f"/api/monitors/{mon['id']}/run-now").json()
    assert not chk["ok"] and chk["failed"] == 1 and chk["total"] == 2
    assert client.get("/api/monitors").json()["failing"] == 1
    checks = client.get(f"/api/monitors/{mon['id']}/checks").json()
    assert len(checks) == 2 and checks[0]["results"][1]["status_code"] == 404
    assert checks[0]["results"][1]["ok"] is False and checks[0]["results"][0]["ok"] is True

    # disabled: not counted as failing, never due
    client.patch(f"/api/monitors/{mon['id']}", json={"enabled": False})
    assert client.get("/api/monitors").json()["failing"] == 0
    assert db.due_monitors(time.time() + 10_000) == []
    assert client.delete(f"/api/monitors/{mon['id']}").json()["ok"]
    assert client.get("/api/monitors").json()["monitors"] == []


def test_monitor_scheduler_and_webhook(client, echo_url):
    import asyncio

    from pyhttp import monitors as mons

    rid = client.post("/api/requests", json={"name": "Hooked", "url": echo_url + "/nope"}).json()["id"]
    client.post("/api/monitors", json={"name": "Alert", "request_ids": [rid], "interval_s": 60,
                                       "webhook_url": echo_url + "/hook"})
    HOOKS.clear()

    async def go():
        sched = mons.MonitorScheduler()
        tasks = sched.tick()           # never ran -> due at once
        assert len(tasks) == 1
        await asyncio.gather(*tasks)
        assert sched.tick() == []      # just ran -> not due for another 60 s

    asyncio.run(go())
    assert HOOKS and HOOKS[-1]["monitor"] == "Alert"
    assert HOOKS[-1]["failed"] == 1 and HOOKS[-1]["results"][0]["status_code"] == 404
    mon = client.get("/api/monitors").json()["monitors"][0]
    assert mon["last_status"] == "fail" and mon["last_run_at"]


def test_send_stream(client, echo_url):
    """/api/send-stream: NDJSON events — status first, chunks as they arrive, then the full result."""
    with client.stream("POST", "/api/send-stream", json={"name": "SSE", "url": echo_url + "/sse"}) as r:
        assert r.status_code == 200
        events = [json.loads(line) for line in r.iter_lines() if line.strip()]
    assert events[0]["type"] == "status"
    assert events[0]["status_code"] == 200 and "text/event-stream" in events[0]["content_type"]
    chunks = "".join(e["text"] for e in events if e["type"] == "chunk")
    assert "data: tick 0" in chunks and "data: tick 3" in chunks
    done = events[-1]
    assert done["type"] == "done" and done["result"]["status_code"] == 200
    assert done["result"]["response"]["body_text"].count("tick") == 4
    # recorded in history like any other send
    h = client.get("/api/history").json()
    assert h and h[0]["status_code"] == 200 and h[0]["request_name"] == "SSE"


# ------------------------------------------------------------- mock servers
def test_mock_server_answers_on_the_same_port(client):
    """Mocks are served by the app itself, under /mock/<slug>, on whatever port it has."""
    mock = client.post("/api/mocks", json={"name": "Payments API"}).json()
    assert mock["slug"] == "payments-api" and mock["base_path"] == "/mock/payments-api"
    assert mock["url"].endswith("/mock/payments-api")
    mid = mock["id"]

    client.post(f"/api/mocks/{mid}/routes", json={
        "name": "one user", "method": "GET", "path": "/users/:id",
        "body": '{"id": "{{req.params.id}}", "q": "{{req.query.expand}}"}',
        "headers": [{"key": "X-Mock", "value": "yes"}]})
    client.post(f"/api/mocks/{mid}/routes", json={
        "name": "boom", "method": "GET", "path": "/users/:id", "status_code": 500,
        "match_query": "fail=1", "body": '{"error": "declined"}'})
    client.post(f"/api/mocks/{mid}/routes", json={"name": "me", "method": "GET", "path": "/users/me",
                                                  "body": '{"me": true}'})
    client.post(f"/api/mocks/{mid}/routes", json={"method": "POST", "path": "/users", "status_code": 201,
                                                  "body": '{"created": "{{req.json.name}}"}'})
    client.post(f"/api/mocks/{mid}/routes", json={"method": "ANY", "path": "/any/**",
                                                  "body": '{"m": "{{req.method}}", "rest": "{{req.params.rest}}"}'})

    r = client.get("/mock/payments-api/users/42?expand=all")
    assert r.status_code == 200 and r.headers["x-mock"] == "yes"
    assert r.json() == {"id": "42", "q": "all"}
    # a query condition makes the same path give another answer, and beats the plain route
    assert client.get("/mock/payments-api/users/42?fail=1").json() == {"error": "declined"}
    assert client.get("/mock/payments-api/users/42?fail=1").status_code == 500
    # an exact path wins over a :param one whatever the order
    assert client.get("/mock/payments-api/users/me").json() == {"me": True}
    # the request body is readable from the template
    assert client.post("/mock/payments-api/users", json={"name": "Ada"}).json() == {"created": "Ada"}
    # ANY answers every method, ** keeps the rest of the path
    assert client.delete("/mock/payments-api/any/a/b").json() == {"m": "DELETE", "rest": "a/b"}
    # HEAD is answered by the GET route, without a body
    head = client.head("/mock/payments-api/users/42")
    assert head.status_code == 200 and head.content == b""

    # nothing matches: a 404 that says what this mock does know
    miss = client.get("/mock/payments-api/nope")
    assert miss.status_code == 404 and miss.json()["error"] == "no_mock_route"
    assert "GET /users/me" in miss.json()["routes"]
    # an unknown slug, and a mock that is turned off
    assert client.get("/mock/nobody/x").status_code == 404
    client.patch(f"/api/mocks/{mid}", json={"enabled": False})
    assert client.get("/mock/payments-api/users/1").status_code == 503
    client.patch(f"/api/mocks/{mid}", json={"enabled": True})

    # CORS preflight is answered even though no OPTIONS route exists
    pre = client.options("/mock/payments-api/users/1",
                         headers={"Origin": "http://localhost:3000", "Access-Control-Request-Headers": "x-key"})
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert pre.headers["access-control-allow-headers"] == "x-key"

    # hits are counted per server and per route
    listed = client.get("/api/mocks").json()
    assert listed["enabled"] == 1 and listed["mocks"][0]["hits"] >= 8
    assert {r["name"]: r["hits"] for r in listed["mocks"][0]["routes"]}["me"] == 1
    # /mock lists what is being mocked on this port
    assert "GET /users/me" in client.get("/mock").json()["mock_servers"][0]["routes"]


def test_mock_variables_delay_calls_and_seeding(client, echo_url):
    """Saved variables and {{mock_url}} in templates, per-route delay, the call log, and
    building routes from requests that have already run."""
    client.put("/api/variables", json={"variables": {"tier": "gold"}})
    mid = client.post("/api/mocks", json={"name": "Shop", "delay_ms": 20}).json()["id"]
    client.post(f"/api/mocks/{mid}/routes", json={
        "path": "/plan", "delay_ms": 30, "content_type": "text/plain", "body": "tier={{tier}} id={{$randomInt 5 5}}"})

    started = time.time()
    r = client.get("/mock/shop/plan")
    assert r.text == "tier=gold id=5"
    assert r.headers["content-type"].startswith("text/plain")
    assert time.time() - started >= 0.05        # the server delay plus the route delay

    # the call log keeps the headers and body a request arrived with
    client.post("/mock/shop/plan?x=1", json={"hello": "world"}, headers={"X-Trace": "t-1"})
    calls = client.get(f"/api/mocks/{mid}/calls").json()
    assert calls[0]["method"] == "POST" and calls[0]["query"] == "x=1" and calls[0]["matched"] is False
    assert calls[0]["status_code"] == 404 and calls[0]["req_headers"]["x-trace"] == "t-1"
    assert json.loads(calls[0]["req_body"]) == {"hello": "world"}
    assert calls[1]["matched"] is True and calls[1]["route_name"] == "GET /plan"
    assert client.delete(f"/api/mocks/{mid}/calls").json()["cleared"] == 2
    assert client.get(f"/api/mocks/{mid}/calls").json() == []

    # routes from a request: its path, and the response it last gave
    rid = client.post("/api/requests", json={"name": "Echo", "method": "POST",
                                             "url": echo_url + "/echo?a=1"}).json()["id"]
    client.post("/api/send", json=client.get(f"/api/requests/{rid}").json())
    out = client.post(f"/api/mocks/{mid}/from-requests", json={"request_ids": [rid]}).json()
    assert out["added"] == 1
    seeded = out["routes"][-1]
    assert seeded["method"] == "POST" and seeded["path"] == "/echo" and seeded["name"] == "Echo"
    assert json.loads(seeded["body"])["method"] == "POST"      # the echo server's own answer
    assert client.post("/mock/shop/echo").json()["method"] == "POST"

    # {{mock_url}} resolves to this app's own /mock base, so requests follow the port
    resolved = client.post("/api/resolve", json={"name": "x", "url": "{{mock_url}}/shop/plan"}).json()
    assert resolved["url"].endswith("/mock/shop/plan")


def test_mock_routes_edit_reorder_and_slugs(client):
    a = client.post("/api/mocks", json={"name": "Billing"}).json()
    b = client.post("/api/mocks", json={"name": "Billing"}).json()
    assert (a["slug"], b["slug"]) == ("billing", "billing-2")   # slugs stay unique
    mid = a["id"]

    r1 = client.post(f"/api/mocks/{mid}/routes", json={"path": "/x", "body": '{"n": 1}'}).json()
    r2 = client.post(f"/api/mocks/{mid}/routes", json={"path": "/x", "body": '{"n": 2}'}).json()
    assert client.get("/mock/billing/x").json() == {"n": 1}     # ties go to the earlier route
    client.post(f"/api/mocks/{mid}/routes/reorder", json={"route_ids": [r2["id"], r1["id"]]})
    assert client.get("/mock/billing/x").json() == {"n": 2}
    client.patch(f"/api/mocks/{mid}/routes/{r2['id']}", json={"enabled": False})
    assert client.get("/mock/billing/x").json() == {"n": 1}     # a disabled route is skipped
    client.patch(f"/api/mocks/{mid}/routes/{r1['id']}", json={"status_code": 418, "path": "x/"})
    assert client.get("/mock/billing/x").status_code == 418     # paths are normalised

    assert client.delete(f"/api/mocks/{mid}/routes/{r1['id']}").json() == {"ok": True}
    assert client.delete(f"/api/mocks/{mid}/routes/{r1['id']}").status_code == 404
    assert client.post(f"/api/mocks/{mid}/routes", json={"path": "/y", "status_code": 99}).status_code == 400
    assert client.post(f"/api/mocks/{mid}/routes", json={"path": "/y", "method": "TRACE"}).status_code == 400
    assert client.post(f"/api/mocks/{mid}/from-requests", json={"request_ids": []}).status_code == 400

    # deleting a mock takes its routes and call log with it
    assert client.delete("/api/mocks/" + mid).json() == {"ok": True}
    assert client.get("/mock/billing/x").status_code == 404
    assert len(client.get("/api/mocks").json()["mocks"]) == 1


def test_projects_are_separate_workspaces(client):
    from pyhttp import projects as pj

    client.put("/api/settings", json={"theme": "light"})
    client.post("/api/requests", json={"name": "only in default", "method": "GET", "url": "http://x"})
    data_dir = Path(client.get("/api/meta").json()["data_dir"])
    assert client.get("/api/meta").json()["project"]["id"] == "default"
    # create + switch: empty collection, settings copied, registry on disk
    r = client.post("/api/projects", json={"name": "Shop API"})
    assert r.status_code == 200, r.text
    pid = r.json()["current"]
    assert pid != "default" and r.json()["active"] == pid
    assert [p["name"] for p in r.json()["projects"]] == ["Default", "Shop API"]
    assert client.get("/api/requests").json() == []
    assert client.get("/api/settings").json()["theme"] == "light"
    assert (data_dir / "projects" / pid / "pyhttp.db").exists()
    assert client.get("/api/meta").json()["project"]["name"] == "Shop API"
    assert client.get("/api/meta").json()["output_dir"].endswith(str(Path("projects") / pid / "outputs"))
    client.post("/api/requests", json={"name": "only in shop", "method": "GET", "url": "http://y"})
    # runs write into the project's own output folder
    rid = client.get("/api/requests").json()[0]["id"]
    run = client.post("/api/runs", json={"name": "r", "request_ids": [rid]}).json()
    run = _wait_run(client, run["id"])
    assert str(Path("projects") / pid / "outputs") in run["output_dir"]
    # names are unique, case-insensitively; renaming works
    assert client.post("/api/projects", json={"name": "shop api"}).status_code == 400
    assert client.post("/api/projects", json={"name": "  "}).status_code == 400
    assert client.patch(f"/api/projects/{pid}", json={"name": "Shop API v2"}).json()["projects"][1]["name"] == "Shop API v2"
    # the open project cannot be deleted; back to Default brings the old data back
    assert client.delete(f"/api/projects/{pid}").status_code == 409
    r = client.post("/api/projects/default/activate")
    assert r.json()["current"] == "default"
    assert [x["name"] for x in client.get("/api/requests").json()] == ["only in default"]
    # the choice is remembered for the next start
    assert pj.Registry(data_dir).active() == "default"
    client.post(f"/api/projects/{pid}/activate")
    assert pj.Registry(data_dir).active() == pid
    client.post("/api/projects/default/activate")
    # delete removes the files
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert not (data_dir / "projects" / pid).exists()
    assert client.delete(f"/api/projects/{pid}").status_code == 404
    assert client.post("/api/projects/nope/activate").status_code == 404


def test_projects_registry_file_and_cli_project(tmp_path):
    from pyhttp import projects as pj

    reg = pj.Registry(tmp_path)
    assert [p["id"] for p in reg.list()] == ["default"] and reg.active() == "default"
    p = reg.create("QA / Staging")
    assert p["id"].startswith("qa-staging-")
    assert reg.find("qa / staging")["id"] == p["id"] and reg.find(p["id"])["name"] == "QA / Staging"
    reg.set_active(p["id"])
    assert pj.Registry(tmp_path).active() == p["id"]   # re-read from disk
    with pytest.raises(ValueError):
        reg.delete(p["id"])                            # active
    # a corrupt registry file falls back to Default instead of crashing
    (tmp_path / "projects.json").write_text("{not json", encoding="utf-8")
    assert pj.Registry(tmp_path).active() == "default"
    # create_app(project=...) opens a named project without touching the active one
    reg = pj.Registry(tmp_path)
    p = reg.create("Other")
    db.seed_database(reg.paths(p["id"])[0])
    app = create_app(tmp_path, project="other")
    with TestClient(app) as c:
        assert c.get("/api/meta").json()["project"]["id"] == p["id"]
        assert c.get("/api/projects").json()["active"] == "default"
    db.close()
    with pytest.raises(ValueError):
        create_app(tmp_path, project="missing")
    db.close()


def test_light_request_list_and_paging(client):
    big = "x" * 5000
    for i in range(3):
        client.post("/api/requests", json={"name": f"r{i}", "method": "POST", "url": "http://x", "body_type": "json",
                                           "bodies": [{"id": f"b{i}", "name": "main", "body": big, "enabled": True}],
                                           "headers": [{"key": "X", "value": "y", "enabled": True}]})
    full = client.get("/api/requests").json()
    light = client.get("/api/requests?fields=list").json()
    assert len(full) == len(light) == 3
    assert "headers" not in light[0] and light[0]["bodies"] == [{"id": "b0", "name": "main", "enabled": True}]
    assert len(json.dumps(light)) < len(json.dumps(full)) / 10
    # history: filters run in SQL over the whole table, `before` pages back
    from pyhttp import db as _db
    for i in range(30):
        _db.add_history({"ts": 1000 + i, "method": "GET", "url": f"http://h/{i}", "request_name": "h",
                         "status": "ok" if i % 3 else "error", "status_code": None if i % 3 == 0 else (200 if i % 2 else 404)})
    page = client.get("/api/history?limit=10").json()
    assert [h["url"] for h in page][:2] == ["http://h/29", "http://h/28"]
    older = client.get(f"/api/history?limit=10&before={page[-1]['ts']}").json()
    assert older[0]["url"] == "http://h/19" and len(older) == 10
    assert all(h["status_code"] is None for h in client.get("/api/history?status=error").json())
    assert len(client.get("/api/history?status=error").json()) == 10
    assert all(h["status_code"] == 404 for h in client.get("/api/history?status=4xx").json())
    assert [h["url"] for h in client.get("/api/history?q=h/2").json()] == [f"http://h/{i}" for i in (29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 2)]
    assert client.get("/api/history?q=%25").json() == []   # LIKE wildcards are literal
    # runs: paging too
    rid = full[0]["id"]
    for _ in range(3):
        _wait_run(client, client.post("/api/runs", json={"request_ids": [rid]}).json()["id"])
    runs = client.get("/api/runs?limit=2").json()
    assert len(runs) == 2
    assert len(client.get(f"/api/runs?limit=2&before={runs[-1]['started_at']}").json()) == 1


def test_full_export_import_keeps_mocks_monitors_and_links(client):
    tok = client.post("/api/requests", json={"name": "token", "method": "POST", "url": "http://x/token"}).json()
    client.post("/api/requests", json={"name": "me", "method": "GET", "url": "http://x/me",
                                       "options": {"depends": {"request_id": tok["id"], "ttl": 60}}})
    client.post("/api/mocks", json={"name": "Pay", "routes": [{"method": "GET", "path": "/ping", "status_code": 200, "body": "{}"}]})
    client.post("/api/monitors", json={"name": "Health", "request_ids": [tok["id"]], "interval_s": 60})
    exp = client.get("/api/export").json()
    assert len(exp["mocks"]) == 1 and exp["mocks"][0]["routes"][0]["path"] == "/ping"
    assert exp["monitors"][0]["request_ids"] == [tok["id"]]
    assert all(r["id"] for r in exp["requests"])
    det = client.post("/api/import/detect", json={"data": exp}).json()
    assert det["kind"] == "pyhttp" and det["mocks"] == 1 and det["monitors"] == 1
    # into a fresh project: everything comes back and the links point at the new ids
    client.post("/api/projects", json={"name": "Copy"})
    res = client.post("/api/import", json={**exp, "replace": False}).json()
    assert res["mocks"] == 1 and res["monitors"] == 1 and res["imported"] == 2
    reqs = {r["name"]: r for r in client.get("/api/requests").json()}
    assert reqs["me"]["options"]["depends"]["request_id"] == reqs["token"]["id"] != tok["id"]
    mons = client.get("/api/monitors").json()
    mons = mons["monitors"] if isinstance(mons, dict) else mons
    assert mons[0]["request_ids"] == [reqs["token"]["id"]]
    mocks = client.get("/api/mocks").json()
    mocks = mocks["mocks"] if isinstance(mocks, dict) else mocks
    assert mocks[0]["name"] == "Pay" and len(mocks[0]["routes"]) == 1
    # replace wipes mocks/monitors too when the file carries them
    res = client.post("/api/import", json={**exp, "replace": True}).json()
    mocks = client.get("/api/mocks").json()
    mocks = mocks["mocks"] if isinstance(mocks, dict) else mocks
    assert len(mocks) == 1
    # docs export
    d = client.get("/api/export/docs").json()
    assert d["filename"].endswith(".md") and "## Contents" in d["markdown"] and "`POST` `http://x/token`" in d["markdown"]
    assert "Sends **token** first." in d["markdown"]


def test_tools_cli(tmp_path, capsys):
    from pyhttp import tools

    assert tools.projects_main(["create", "QA", "--use"], tmp_path) == 0
    assert tools.projects_main(["list"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "* QA" in out and "  Default" in out
    assert tools.projects_main(["rename", "qa", "QA2"], tmp_path) == 0
    assert tools.projects_main(["delete", "QA2", "--yes"], tmp_path) == 2      # active
    assert tools.projects_main(["use", "default"], tmp_path) == 0
    assert tools.projects_main(["delete", "QA2", "--yes"], tmp_path) == 0
    assert tools.projects_main(["use", "nothing"], tmp_path) == 2
    capsys.readouterr()
    # doctor: an orphan output folder is reported, old ones are cleaned
    db.seed_database(tmp_path / "pyhttp.db")
    old = tmp_path / "outputs" / "2020-01-01_old"
    old.mkdir(parents=True)
    (old / "a.txt").write_text("x")
    os.utime(old, (0, 0))
    assert tools.doctor_main(["--clean-outputs", "30", "--yes"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "integrity ok" in out and "orphans:  1" in out and "Deleted 1 folder" in out
    assert not old.exists()


def test_update_repo_never_points_at_the_old_releases_repo(client, monkeypatch):
    from pyhttp import app as app_mod

    assert app_mod.github_repo() == "pyhttp2/pyhttp"
    # a setting left over from before the move is ignored, a real override is honoured
    client.put("/api/settings", json={"update_repo": "pyhttp2/pyhttp-releases"})
    assert app_mod.github_repo() == "pyhttp2/pyhttp"
    client.put("/api/settings", json={"update_repo": "someone/fork"})
    assert app_mod.github_repo() == "someone/fork"
    client.put("/api/settings", json={"update_repo": ""})
    monkeypatch.setenv("PYHTTP_GITHUB_REPO", "PyHTTP2/pyhttp-releases/")
    assert app_mod.github_repo() == "pyhttp2/pyhttp"
    monkeypatch.setattr(app_mod, "_BUILT_REPO", "pyhttp2/pyhttp-releases")
    assert app_mod.github_repo() == "pyhttp2/pyhttp"


def test_updater_reads_the_cask_version_and_waits_for_it(monkeypatch):
    from pyhttp import updater

    assert updater.parse_cask_version('cask "pyhttp" do\n  version "2.0.5"\n  sha256 "x"\nend') == "2.0.5"
    assert updater.parse_cask_version("nothing") is None
    assert updater.version_tuple("2.0.10") > updater.version_tuple("v2.0.9")
    # a brew install learns from the update check whether the tap already offers the release
    monkeypatch.setattr(updater, "install_kind", lambda: "brew")
    monkeypatch.setattr(updater, "tap_cask_version", lambda timeout=6.0: "2.0.4")
    inf = updater.info("2.0.5")
    assert inf["cask_version"] == "2.0.4" and inf["ready"] is False
    monkeypatch.setattr(updater, "tap_cask_version", lambda timeout=6.0: "2.0.5")
    assert updater.info("2.0.5")["ready"] is True
    monkeypatch.setattr(updater, "tap_cask_version", lambda timeout=6.0: None)
    assert updater.info("2.0.5")["ready"] is None
    assert "ready" not in updater.info()          # /api/meta: no network call
    # the brew run re-runs `brew update` while the cask lags, then upgrades once it is there
    calls = []
    offered = iter(["2.0.4", "2.0.4", "2.0.5"])
    u = updater.Updater(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(updater, "brew_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(updater, "tap_remote", lambda brew, env: (Path("/taps/pyhttp"), updater.TAP_URL))
    monkeypatch.setattr(updater, "local_cask_version", lambda brew, env, log=None: next(offered))
    monkeypatch.setattr(updater, "brew_caskroom", lambda: None)
    monkeypatch.setattr(updater, "BREW_WAIT_SECONDS", 0)
    monkeypatch.setattr(updater, "_quit_later", lambda *a, **k: None)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: type("P", (), {"wait": lambda self: 0})())
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(u, "_run_logged", lambda cmd, env, check=True: calls.append(cmd[1:]))
    u._run_brew("2.0.5")
    # the tap is trusted first (Homebrew 7), the cask is addressed by its tap-qualified token
    assert calls == [["trust", updater.TAP], ["update"], ["update"], ["update"], ["upgrade", "--cask", updater.QUALIFIED_CASK]]
    # still lagging after every round: a clear error, no upgrade attempt
    calls.clear()
    monkeypatch.setattr(updater, "local_cask_version", lambda brew, env, log=None: "2.0.4")
    monkeypatch.setattr(updater, "BREW_WAIT_ROUNDS", 1)
    with pytest.raises(RuntimeError, match="still offers 2.0.4"):
        u._run_brew("2.0.5")
    assert not any(c[0] == "upgrade" for c in calls)


def test_updater_repoints_a_tap_that_still_fetches_the_old_repository(monkeypatch):
    from pyhttp import updater

    assert updater.tap_needs_repointing("https://github.com/pyhttp2/pyhttp-releases")
    assert updater.tap_needs_repointing("git@github.com:pyhttp2/pyhttp-releases.git")
    assert not updater.tap_needs_repointing("https://github.com/pyhttp2/homebrew-pyhttp")
    assert not updater.tap_needs_repointing("")
    calls = []
    u = updater.Updater(Path(tempfile.mkdtemp()))
    monkeypatch.setattr(updater, "brew_path", lambda: "/usr/bin/true")
    tap_dir = Path(tempfile.gettempdir()) / "taps" / "pyhttp"   # a real-looking path on every OS
    monkeypatch.setattr(updater, "tap_remote", lambda brew, env: (tap_dir, "https://github.com/pyhttp2/pyhttp-releases"))
    monkeypatch.setattr(updater, "local_cask_version", lambda brew, env, log=None: "2.0.6")
    monkeypatch.setattr(updater, "brew_caskroom", lambda: None)
    monkeypatch.setattr(updater, "_quit_later", lambda *a, **k: None)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: type("P", (), {"wait": lambda self: 0})())
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(u, "_run_logged", lambda cmd, env, check=True: calls.append(cmd))
    u._run_brew("2.0.6")
    assert calls[0] == ["git", "-C", str(tap_dir), "remote", "set-url", "origin", updater.TAP_URL]
    # re-pointing the remote drops the trust Homebrew recorded for the old URL: trust again, then update
    assert calls[1][1:] == ["trust", updater.TAP]
    assert calls[2][1] == "update" and calls[-1][1:] == ["upgrade", "--cask", updater.QUALIFIED_CASK]
    # not tapped at all: tap it first
    calls.clear()
    monkeypatch.setattr(updater, "tap_remote", lambda brew, env: (None, ""))
    u._run_brew("2.0.6")
    assert calls[0][1:] == ["tap", updater.TAP, updater.TAP_URL]


def test_updater_explains_brew_failures(tmp_path):
    """A failed brew command reports brew's own Error line plus the fix, not just "see the output"."""
    import sys

    from pyhttp import updater

    untrusted = ("Error: Refusing to load cask pyhttp2/pyhttp/pyhttp from untrusted tap pyhttp2/pyhttp.\n"
                 "Run `brew trust --cask pyhttp2/pyhttp/pyhttp` or `brew trust pyhttp2/pyhttp` to trust it.")
    assert "brew trust pyhttp2/pyhttp" in updater.brew_failure_hint(untrusted)
    assert "brew trust pyhttp2/pyhttp" in updater.brew_failure_hint("\x1b[31mError:\x1b[0m Refusing ... untrusted tap x")
    assert "two taps" in updater.brew_failure_hint("Error: Cask pyhttp exists in multiple taps: a/b/pyhttp, c/d/pyhttp")
    assert "checksum" in updater.brew_failure_hint("Error: SHA256 mismatch\nExpected: a\nActual: b")
    assert "Trash" in updater.brew_failure_hint("Error: It seems there is already an App at '/Applications/pyhttp.app'.")
    assert updater.brew_failure_hint("Error: something else") is None
    assert updater.brew_failure_hint("") is None
    # the Error line (and its continuation) is what goes into the message
    lines = ["==> Upgrading 1 outdated package:", "pyhttp2/pyhttp/pyhttp 2.0.6 -> 2.0.7", "",
             "\x1b[31mError:\x1b[0m Refusing to load cask from untrusted tap pyhttp2/pyhttp.", "Run `brew trust pyhttp2/pyhttp` to trust it."]
    assert updater.failure_detail(lines) == ("Error: Refusing to load cask from untrusted tap pyhttp2/pyhttp. "
                                             "Run `brew trust pyhttp2/pyhttp` to trust it.")
    assert updater.failure_detail(["==> step", "last line"]) == "==> step / last line"
    assert updater.failure_detail([]) == "no output"
    # end to end through _run_logged with a real failing process
    u = updater.Updater(tmp_path)
    cmd = [sys.executable, "-c", "import sys; print('==> Upgrading'); print('Error: Refusing to load cask x from untrusted tap pyhttp2/pyhttp.'); sys.exit(1)"]
    with pytest.raises(RuntimeError) as err:
        u._run_logged(cmd, dict(os.environ))
    msg = str(err.value)
    assert "exit code 1" in msg and "untrusted tap pyhttp2/pyhttp" in msg and "brew trust pyhttp2/pyhttp" in msg
    assert any("==> Upgrading" in line for line in u.status()["log"])
    # check=False: the exit code and output come back, nothing is raised (used for `brew trust`)
    rc, out = u._run_logged(cmd, dict(os.environ), check=False)
    assert rc == 1 and any("Error:" in line for line in out)
    rc, out = u._run_logged([sys.executable, "-c", "print('ok')"], dict(os.environ))
    assert rc == 0 and out == ["ok"]
    # the terminal fallback for a brew install trusts the tap first and uses the qualified token
    cmds = updater._commands("brew")[2]
    assert cmds[0] == f"brew trust {updater.TAP}" and f"brew upgrade --cask {updater.QUALIFIED_CASK}" in cmds


# ------------------------------------------------------------------ git sync
def _git(cwd, *args: str) -> str:
    import subprocess

    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


needs_git = pytest.mark.skipif(not __import__("shutil").which("git"), reason="git is not installed")


def test_git_tree_layout_and_secrets():
    from pyhttp import gitsync

    payload = {
        "project": "Shop", "variables": {"base": "http://x"},
        "folders": [{"id": "f1", "name": "Users", "parent_id": None, "variables": [{"key": "k", "value": "s3", "secret": True}]},
                    {"id": "f2", "name": "Admin: v2?", "parent_id": "f1", "variables": []}],
        "requests": [{"id": "a1", "name": "List", "folder_id": "f1"}, {"id": "a2", "name": "list", "folder_id": "f1"},
                     {"id": "a3", "name": "CON", "folder_id": "f2"}, {"id": "a4", "name": "_folder", "folder_id": None}],
        "environments": [{"name": "Prod", "variables": [{"key": "token", "value": "hunter2", "secret": True},
                                                        {"key": "host", "value": "prod", "secret": False}]}],
        "mocks": [{"name": "Fake", "slug": "fake", "routes": []}], "monitors": [],
    }
    files = gitsync.build_tree(payload)
    assert set(files) == {
        "pyhttp.json", "variables.json", "monitors.json", "environments/Prod.json", "mocks/fake.json",
        "collection/Users/_folder.json", "collection/Users/List.json", "collection/Users/list (a2).json",
        "collection/Users/Admin v2/_folder.json", "collection/Users/Admin v2/CON_.json", "collection/_folder (a4).json"}
    prod = json.loads(files["environments/Prod.json"])
    assert [v["value"] for v in prod["variables"]] == ["", "prod"]          # secret values stay out by default
    assert json.loads(files["collection/Users/_folder.json"])["variables"][0]["value"] == ""
    assert "hunter2" in gitsync.build_tree(payload, secrets=True)["environments/Prod.json"]


@needs_git
def test_git_sync_commits_and_pushes_on_change(client, tmp_path):
    from pyhttp import gitsync

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    work = tmp_path / "work"
    assert client.get("/api/git").json()["state"] == "off"
    folder = client.post("/api/folders", json={"name": "Users"}).json()
    r = client.post("/api/requests", json={"name": "Login", "url": "http://x/login", "folder_id": folder["id"]}).json()

    # turning it on writes everything, commits and pushes at once (the defaults are automatic)
    st = client.put("/api/git", json={"dir": str(work), "remote": str(remote)}).json()
    assert st["state"] == "ok" and st["error"] == "", st
    assert st["auto_commit"] and st["auto_push"] and not st["secrets"]
    assert st["commit"] and not st["unpushed"] and not st["dirty"] and st["remote_name"] == "origin"
    assert json.loads((work / "collection" / "Users" / "Login.json").read_text(encoding="utf-8"))["url"] == "http://x/login"
    assert _git(remote, "log", "--oneline").count("\n") == 0   # one commit arrived

    # an edit schedules a sync by itself; the commit names what changed and reaches the remote
    gitsync.Syncer.DELAY, old_delay = 0.2, gitsync.Syncer.DELAY
    try:
        client.put(f"/api/requests/{r['id']}", json={**r, "url": "http://x/v2/login"})
        assert client.get("/api/git").json()["state"] in ("pending", "syncing")
        for _ in range(100):
            if "v2" in _git(remote, "show", "HEAD:collection/Users/Login.json"):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the edit was not pushed")
        assert _git(remote, "log", "-1", "--format=%s") == "pyhttp: update Login"
        # sending a request is not an edit: nothing is scheduled
        client.post("/api/send", json={"name": "x", "url": "http://127.0.0.1:9/none", "timeout": 1})
        assert client.get("/api/git").json()["state"] == "ok"
    finally:
        gitsync.Syncer.DELAY = old_delay

    # renaming moves the file, deleting removes it; nothing stale is left behind
    client.put(f"/api/requests/{r['id']}", json={**r, "name": "Sign in"})
    st = client.post("/api/git/sync").json()
    assert (work / "collection" / "Users" / "Sign in.json").exists() and not (work / "collection" / "Users" / "Login.json").exists()
    client.delete(f"/api/folders/{folder['id']}")
    st = client.post("/api/git/sync").json()
    assert st["state"] == "ok" and not (work / "collection" / "Users").exists()
    assert "collection/Users" not in _git(remote, "ls-tree", "-r", "--name-only", "HEAD")

    # with the switches off nothing is committed until "Sync now"
    st = client.put("/api/git", json={"auto_commit": False, "auto_push": False}).json()
    client.post("/api/requests", json={"name": "Later", "url": "http://x/later"})
    before = _git(work, "rev-parse", "HEAD")
    st = client.put("/api/git", json={}).json()   # a plain sync: files written, no commit
    assert st["dirty"] and (work / "collection" / "Later.json").exists() and _git(work, "rev-parse", "HEAD") == before
    st = client.post("/api/git/sync").json()
    assert not st["dirty"] and not st["unpushed"] and _git(remote, "rev-parse", "HEAD") == _git(work, "rev-parse", "HEAD")

    # off: an empty folder setting; the repository stays
    assert client.put("/api/git", json={"dir": ""}).json()["state"] == "off"
    assert client.post("/api/git/sync").status_code == 400
    assert (work / ".git").exists()


@needs_git
def test_git_sync_inside_an_existing_repository_leaves_other_work_alone(client, tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init")
    (repo / "main.py").write_text("print('wip')\n", encoding="utf-8")       # the user's own uncommitted work
    client.post("/api/requests", json={"name": "Ping", "url": "http://x/ping"})
    st = client.put("/api/git", json={"dir": str(repo / "api requests")}).json()
    assert st["state"] == "ok", st
    assert not st["remote_name"] and not st["unpushed"]                      # no remote: commits stay local, no error
    assert Path(st["repo"]).resolve() == repo.resolve()
    assert _git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines() == [
        "api requests/collection/Ping.json", "api requests/monitors.json", "api requests/pyhttp.json", "api requests/variables.json"]
    assert "?? main.py" in _git(repo, "status", "--porcelain")

    # a folder that already holds a "collection" pyhttp did not write is refused, not overwritten
    other = tmp_path / "other"
    (other / "collection").mkdir(parents=True)
    (other / "collection" / "mine.json").write_text("{}", encoding="utf-8")
    st = client.put("/api/git", json={"dir": str(other)}).json()
    assert st["state"] == "error" and "did not write" in st["error"]
    assert (other / "collection" / "mine.json").exists()
    assert client.put("/api/git", json={"dir": "relative/path"}).status_code == 400
