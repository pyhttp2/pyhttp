"""Tags on flows, monitors and mock servers (requests had them already), with the same limits."""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pyhttp import db  # noqa: E402
from tests.test_backend import client, echo_url  # noqa: E402, F401 - fixtures


def test_flows_monitors_and_mocks_carry_tags(client: TestClient) -> None:
    flow = client.post("/api/flows", json={"name": "Sign-up", "tags": ["#smoke", " auth ", "smoke"]}).json()
    assert flow["tags"] == ["smoke", "auth"]   # a leading # dropped, trimmed, each once
    assert client.put(f"/api/flows/{flow['id']}", json={"tags": ["nightly"]}).json()["tags"] == ["nightly"]
    assert client.put(f"/api/flows/{flow['id']}", json={"name": "Sign-up 2"}).json()["tags"] == ["nightly"]   # untouched when not sent
    assert client.get("/api/flows").json()["flows"][0]["tags"] == ["nightly"]

    req = client.post("/api/requests", json={"name": "ping", "method": "GET", "url": "http://example.com"}).json()
    mon = client.post("/api/monitors", json={"name": "Prod", "request_ids": [req["id"]], "tags": ["prod"]}).json()
    assert mon["tags"] == ["prod"]
    assert client.patch(f"/api/monitors/{mon['id']}", json={"tags": ["prod", "eu"]}).json()["tags"] == ["prod", "eu"]
    assert client.get("/api/monitors").json()["monitors"][0]["tags"] == ["prod", "eu"]

    mock = client.post("/api/mocks", json={"name": "Payments", "tags": ["payments"]}).json()
    assert mock["tags"] == ["payments"]
    assert client.patch(f"/api/mocks/{mock['id']}", json={"tags": []}).json()["tags"] == []
    assert client.post("/api/mocks", json={"name": "No tags"}).json()["tags"] == []


def test_tag_limits_hold_whatever_the_client_sends(client: TestClient) -> None:
    many = [f"t{i}" for i in range(db.MAX_TAGS + 5)]
    long = "x" * (db.MAX_TAG_LENGTH + 10)
    flow = client.post("/api/flows", json={"name": "f", "tags": many}).json()
    assert flow["tags"] == many[:db.MAX_TAGS]
    assert client.put(f"/api/flows/{flow['id']}", json={"tags": [long]}).json()["tags"] == ["x" * db.MAX_TAG_LENGTH]
    req = client.post("/api/requests", json={"name": "r", "method": "GET", "url": "http://example.com", "tags": many}).json()
    assert len(req["tags"]) == db.MAX_TAGS
    assert db.MAX_TAGS == 20 and db.MAX_TAG_LENGTH == 32   # the UI says these numbers (app.js TAG_MAX, TAG_MAX_LEN)


def test_a_database_from_before_tags_gets_them(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""CREATE TABLE flows (id TEXT PRIMARY KEY, name TEXT NOT NULL, graph TEXT NOT NULL DEFAULT '{}',
                          created_at REAL NOT NULL, updated_at REAL NOT NULL, last_run_at REAL, last_status TEXT);
                          INSERT INTO flows VALUES ('a', 'old', '{}', 1, 1, NULL, NULL);""")
    conn.commit()
    conn.close()
    db.seed_database(path)   # schema + migrations, as opening a project does
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT tags FROM flows WHERE id = 'a'").fetchone()[0] == "[]"
    conn.close()
