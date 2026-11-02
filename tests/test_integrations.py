"""OAuth 2.0, secret variables, OpenAPI / HAR import and the `pyhttp run` CLI.

Uses the echo server and fixtures from test_backend.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp import cli, db, oauth2, openapi, postman  # noqa: E402
from pyhttp.app import create_app  # noqa: E402
from tests.test_backend import OAUTH, _wait_run, client, echo_url  # noqa: E402,F401 - fixtures


# ------------------------------------------------------- OAuth 2.0 / secrets
def test_oauth2_client_credentials_and_password(client, echo_url):
    oauth2.forget()
    OAUTH["issued"].clear()
    OAUTH["revoked"].clear()
    OAUTH["calls"].clear()
    auth = {"type": "oauth2", "grant": "client_credentials", "token_url": "{{base}}/oauth/token",
            "client_id": "cid", "client_secret": "{{secret}}", "scope": "read write"}
    client.put("/api/variables", json={"variables": {"base": echo_url, "secret": "csecret"}})
    req = {"name": "p", "method": "GET", "url": echo_url + "/protected", "auth": auth}
    r = client.post("/api/send", json=req).json()
    assert r["status_code"] == 200 and json.loads(r["response"]["body_text"]) == {"token": "tok1"}
    assert OAUTH["calls"][0]["grant"] == "client_credentials" and OAUTH["calls"][0]["form"]["scope"] == "read write"
    # cached: no second token request
    r = client.post("/api/send", json=req).json()
    assert r["status_code"] == 200 and len(OAUTH["calls"]) == 1
    st = client.post("/api/oauth2/status", json=req).json()
    assert st["has_token"] and st["valid"] and st["preview"] == "tok1"
    # revoked token: one 401, a new token, then success
    OAUTH["revoked"].add("tok1")
    r = client.post("/api/send", json=req).json()
    assert r["status_code"] == 200 and json.loads(r["response"]["body_text"]) == {"token": "tok2"}
    # resolve shows the cached token
    res = client.post("/api/resolve", json=req).json()
    assert ["Authorization", "Bearer tok2"] in [list(h) for h in res["headers"]]
    # get a new token on demand, then forget it
    assert client.post("/api/oauth2/token", json=req).json()["preview"] == "tok3"
    assert client.post("/api/oauth2/forget", json=req).json() == {"has_token": False}
    # password grant with client credentials in the body, inherited from a folder
    folder = client.post("/api/folders", json={"name": "F"}).json()
    client.patch(f"/api/folders/{folder['id']}", json={"auth": {**auth, "grant": "password", "username": "u",
                                                               "password": "pw", "client_auth": "body"}})
    r = client.post("/api/send", json={**req, "auth": {"type": "inherit"}, "folder_id": folder["id"]}).json()
    assert r["status_code"] == 200
    call = OAUTH["calls"][-1]
    assert call["grant"] == "password" and call["form"]["client_id"] == "cid" and call["form"]["username"] == "u"
    # bad credentials: a readable error, nothing sent
    r = client.post("/api/send", json={**req, "auth": {**auth, "client_secret": "nope", "scope": "x"}}).json()
    assert r["status"] == "error" and "token request failed: HTTP 401" in r["error"]
    assert client.post("/api/oauth2/status", json={**req, "auth": {"type": "basic"}}).status_code == 400
    # Postman round trip keeps the OAuth settings
    back = postman._auth(postman._pm_auth({**db.normalize_auth(auth), "grant": "password", "username": "u"}))
    assert back["type"] == "oauth2" and back["grant"] == "password" and back["token_url"] == "{{base}}/oauth/token"


def test_secret_variables_are_masked_in_history_and_runs(client, echo_url):
    env = client.post("/api/environments", json={"name": "Prod", "variables": [
        {"key": "apikey", "value": "s3cr3t-value", "secret": True}, {"key": "plain", "value": "visible"}]}).json()
    assert env["variables"][0]["secret"] is True and "secret" not in env["variables"][1]
    client.put("/api/environments/active", json={"id": env["id"]})
    req = {"name": "S", "method": "GET", "url": echo_url + "/echo?k={{apikey}}",
           "headers": [{"key": "X-Key", "value": "{{apikey}}"}, {"key": "X-Plain", "value": "{{plain}}"}]}
    live = client.post("/api/send", json=req).json()
    echoed = json.loads(live["response"]["body_text"])
    assert echoed["headers"]["x-key"] == "s3cr3t-value" and echoed["query"] == {"k": "s3cr3t-value"}  # really sent
    h = client.get("/api/history").json()[0]
    assert "s3cr3t" not in h["url"]
    detail = client.get(f"/api/history/{h['id']}").json()["detail"]
    assert "s3cr3t" not in json.dumps(detail) and detail["request"]["url"].endswith("?k=\u2022\u2022\u2022\u2022\u2022\u2022")
    headers = {k.lower(): v for k, v in detail["request"]["headers"]}
    assert headers["x-key"] == "••••••" and headers["x-plain"] == "visible"
    saved = client.post("/api/requests", json=req).json()
    run = _wait_run(client, client.post("/api/runs", json={"request_ids": [saved["id"]]}).json()["id"])
    assert "s3cr3t" not in json.dumps(run["results"][0]["detail"]["request"])
    out = Path(run["output_dir"])
    # the request side of the saved files is masked; the response is the server's answer, kept as-is
    saved = json.loads((out / "01_S.json").read_text(encoding="utf-8"))
    assert "s3cr3t" not in json.dumps(saved["request"]) and "s3cr3t" not in (out / "summary.json").read_text(encoding="utf-8")
    # Postman environment export marks it secret, import keeps it
    pm = postman.to_environment(client.get("/api/environments").json()["environments"][0])
    assert pm["values"][0]["type"] == "secret" and pm["values"][1]["type"] == "default"
    assert postman.from_environment(pm)["variables"][0]["secret"] is True


# ---------------------------------------------------------- OpenAPI / HAR
SPEC = {
    "openapi": "3.0.1",
    "info": {"title": "Pets", "version": "1.2", "description": "Pet store"},
    "servers": [{"url": "https://{env}.example.com/v1", "variables": {"env": {"default": "api"}}}],
    "security": [{"bearer": []}],
    "tags": [{"name": "pets", "description": "Pet operations"}],
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"},
                            "key": {"type": "apiKey", "in": "header", "name": "X-Key"}},
        "schemas": {
            "Pet": {"type": "object", "required": ["name"], "properties": {
                "id": {"type": "integer"}, "name": {"type": "string", "example": "Rex"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "owner": {"$ref": "#/components/schemas/Owner"}}},
            "Owner": {"type": "object", "properties": {
                "email": {"type": "string", "format": "email"}, "pet": {"$ref": "#/components/schemas/Pet"}}},
        },
        "parameters": {"Limit": {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}}},
    },
    "paths": {
        "/pets": {
            "get": {"tags": ["pets"], "summary": "List pets", "parameters": [
                {"$ref": "#/components/parameters/Limit"},
                {"name": "X-Trace", "in": "header", "required": True, "schema": {"type": "string"}}]},
            "post": {"tags": ["pets"], "summary": "Create pet", "security": [{"key": []}],
                     "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}}},
        },
        "/pets/{pet-id}": {
            "parameters": [{"name": "pet-id", "in": "path", "required": True, "schema": {"type": "integer", "example": 7}}],
            "delete": {"operationId": "deletePet", "deprecated": True, "security": []},
        },
        "/login": {"post": {"requestBody": {"content": {"application/x-www-form-urlencoded": {
            "schema": {"type": "object", "properties": {"user": {"type": "string"}, "pass": {"type": "string"}}}}}}}},
    },
}


def test_openapi_import(client):
    conv = openapi.from_openapi(SPEC)
    root = conv["folders"][0]
    assert conv["name"] == "Pets"
    assert root["variables"][0] == {"key": "baseUrl", "value": "https://api.example.com/v1", "enabled": True}
    assert root["auth"]["type"] == "bearer" and [f["name"] for f in conv["folders"][1:]] == ["pets"]
    by = {r["name"]: r for r in conv["requests"]}
    lst = by["List pets"]
    assert lst["url"] == "{{baseUrl}}/pets" and lst["params"] == [{"key": "limit", "value": "20", "enabled": False}]
    assert lst["headers"][0]["key"] == "X-Trace" and lst["headers"][0]["enabled"] is True
    create = by["Create pet"]
    body = json.loads(create["body"])
    assert create["body_type"] == "json" and body["name"] == "Rex" and body["owner"]["email"] == "user@example.com"
    assert create["auth"] == {"type": "apikey", "key": "X-Key", "value": "{{apiKey}}", "add_to": "header"}
    delete = by["deletePet"]
    assert delete["url"] == "{{baseUrl}}/pets/:pet_id"
    assert delete["params"][0] == {"key": "pet_id", "value": "7", "enabled": True}
    assert delete["auth"] == {"type": "none"} and "Deprecated" in delete["description"]
    login = by["POST /login"]
    assert login["body_type"] == "form" and login["body"] == "user: string\npass: string"
    # Swagger 2.0
    sw = {"swagger": "2.0", "info": {"title": "Old"}, "host": "old.example.com", "basePath": "/api", "schemes": ["http"],
          "paths": {"/items": {"post": {"parameters": [
              {"in": "body", "name": "b", "schema": {"type": "object", "properties": {"n": {"type": "number"}}}}]}}}}
    c2 = openapi.from_openapi(sw)
    assert c2["folders"][0]["variables"][0]["value"] == "http://old.example.com/api"
    assert json.loads(c2["requests"][0]["body"]) == {"n": 0.0}
    # through the API, YAML included
    import yaml
    data = client.post("/api/import/parse", json={"text": yaml.safe_dump(SPEC)}).json()["data"]
    det = client.post("/api/import/detect", json={"data": data}).json()
    assert det["kind"] == "openapi" and det["requests"] == 4
    res = client.post("/api/import/postman", json={"data": data}).json()
    assert res["kind"] == "openapi" and res["imported"] == 4
    assert client.post("/api/import/parse", json={"text": "a: [unclosed"}).status_code == 400


def test_har_import(client):
    har = {"log": {"version": "1.2", "pages": [{"title": "My app"}], "entries": [
        {"request": {"method": "POST", "url": "https://api.example.com/v1/login?x=1", "headers": [
            {"name": "Content-Type", "value": "application/json"}, {"name": "Cookie", "value": "a=b"},
            {"name": ":authority", "value": "x"}],
            "postData": {"mimeType": "application/json", "text": '{"u":"a"}'}}, "response": {"status": 200}},
        {"request": {"method": "GET", "url": "https://cdn.example.com/app.js", "headers": []}},
        {"request": {"method": "POST", "url": "https://api.example.com/form", "headers": [],
                     "postData": {"mimeType": "application/x-www-form-urlencoded", "params": [{"name": "a", "value": "1"}]}}},
    ]}}
    assert openapi.is_har(har) and not openapi.is_openapi(har)
    conv = openapi.from_har(har)
    assert conv["name"] == "HAR: My app" and len(conv["requests"]) == 2
    login = conv["requests"][0]
    assert login["url"] == "https://api.example.com/v1/login" and login["params"] == [{"key": "x", "value": "1", "enabled": True}]
    assert [h["key"] for h in login["headers"]] == ["Content-Type"] and json.loads(login["body"]) == {"u": "a"}
    assert conv["requests"][1]["body_type"] == "form" and conv["requests"][1]["body"] == "a: 1"
    assert len(openapi.from_har(har, include_assets=True)["requests"]) == 3
    assert client.post("/api/import/detect", json={"data": har}).json()["kind"] == "har"
    assert client.post("/api/import/postman", json={"data": har}).json()["imported"] == 2


# ------------------------------------------------------------------ CLI runner
def test_cli_run(echo_url, tmp_path, capsys):
    data_dir = tmp_path / "data"
    create_app(data_dir)
    root = db.create_folder("API")
    sub = db.create_folder("Users", root["id"])
    db.create_request({"name": "Ok", "method": "GET", "url": "{{base}}/echo", "folder_id": sub["id"],
                       "tests": [{"type": "status", "op": "equals", "value": "200"}], "tags": ["smoke"]})
    db.create_request({"name": "Missing", "method": "GET", "url": "{{base}}/status/404", "folder_id": root["id"]})
    db.create_environment("QA", [{"key": "base", "value": echo_url}])
    db.close()

    junit = tmp_path / "junit.xml"
    assert cli.main(["--data-dir", str(data_dir), "--env", "QA", "Users", "--junit", str(junit), "--no-color"], data_dir) == 0
    out = capsys.readouterr().out
    assert "✓" in out and "1/1 requests passed, 1/1 tests passed" in out
    xml = junit.read_text(encoding="utf-8")
    assert 'tests="1"' in xml and "<failure" not in xml and "[PASS]" in xml

    html_path = tmp_path / "r.html"
    rc = cli.main(["--data-dir", str(data_dir), "-e", "qa", "--html", str(html_path), "--json", str(tmp_path / "r.json"),
                   "--no-color"], data_dir)
    out = capsys.readouterr().out
    assert rc == 1 and "1/2 requests passed" in out and "Missing: HTTP 404" in out
    assert "Missing" in html_path.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["failed"] == 1
    assert cli.main(["--data-dir", str(data_dir), "-e", "QA", "--no-fail-on-http-error", "-n", "2", "-q"], data_dir) == 0
    assert cli.main(["--data-dir", str(data_dir), "-e", "QA", "--tag", "smoke", "-q"], data_dir) == 0
    assert cli.main(["--data-dir", str(data_dir), "nope"], data_dir) == 2
    assert cli.main(["--data-dir", str(data_dir), "--env", "Nope"], data_dir) == 2
    capsys.readouterr()
    assert cli.main(["--data-dir", str(data_dir), "--list", "API", "--no-color"], data_dir) == 0
    assert "API/Users/Ok" in capsys.readouterr().out
    # the active environment is left as it was; the runs show up in Run history
    db.init(data_dir / "pyhttp.db")
    assert db.get_setting("active_env") is None and len(db.list_runs()) == 4
    db.close()

    # straight from a Postman collection file, with --var; nothing lands in the data folder
    coll = postman.to_collection("C", [], [{"id": "1", "name": "Echo", "method": "POST", "url": "{{base}}/echo",
                                             "folder_id": None, "body_type": "json", "body": "{}", "bodies": [],
                                             "params": [], "headers": [], "auth": {"type": "none"}, "tests": [],
                                             "description": ""}], {})
    f = tmp_path / "c.json"
    f.write_text(json.dumps(coll), encoding="utf-8")
    assert cli.main([str(f), "--var", f"base={echo_url}", "--no-color"], data_dir) == 0
    assert "Echo" in capsys.readouterr().out
