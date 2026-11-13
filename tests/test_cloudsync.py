"""Cloud sync, the part that lives in the project database: the change log, writing rows
that came from the cloud, secrets, and leaving no trace when a project is unlinked.
(The conversation with the server is tested with the server.)

Run: python -m pytest tests/test_cloudsync.py -q
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp import cloudsync, db  # noqa: E402


def project(tmp_path: Path) -> tuple[db.Database, sqlite3.Connection]:
    database = db.Database(tmp_path / "pyhttp.db")
    return database, cloudsync._connect(tmp_path / "pyhttp.db")


def test_changes_are_logged_only_for_a_linked_project_and_only_for_synced_tables(tmp_path: Path) -> None:
    database, conn = project(tmp_path)
    with db.using(database):
        db.create_request({"name": "Before", "method": "GET", "url": "https://x"})
        assert not cloudsync.linked(database.path)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%cloud%'").fetchone()[0] == 0   # untouched until linked

        cloudsync.install(conn)
        cloudsync.set_meta(conn, project="p1", cursor=0)
        assert cloudsync.linked(database.path)
        made = db.create_request({"name": "After", "method": "GET", "url": "https://y"})
        db.update_request(made["id"], {**made, "name": "After, renamed"})
        db.set_setting("theme", "light")                       # settings belong to the computer
        upto, pending = cloudsync.take(conn)
        assert upto > 0 and [i["d"]["name"] for i in pending.values()] == ["After, renamed"]   # one entry per row, as it is now
        only = next(iter(pending.values()))
        assert only["t"] == "requests" and json.loads(only["k"]) == [made["id"]] and only["id"] == cloudsync.row_id("requests", only["k"])

        db.delete_request(made["id"])
        assert next(iter(cloudsync.take(conn)[1].values()))["d"] is None   # a deleted row goes up as "gone"

        cloudsync.enqueue_everything(conn)
        assert {i["d"]["name"] for i in cloudsync.take(conn)[1].values() if i["d"]} == {"Before"}


def test_rows_from_the_cloud_are_written_without_counting_as_changes_here(tmp_path: Path) -> None:
    database, conn = project(tmp_path)
    with db.using(database):
        cloudsync.install(conn)
        mine = db.create_request({"name": "Mine", "method": "GET", "url": "https://mine"})
        row = cloudsync._row(conn, "requests", json.dumps([mine["id"]]))
        conn.execute("DELETE FROM _cloud_changes")
        conn.commit()

        theirs = {**row, "id": "from-the-cloud", "name": "Theirs"}
        cloudsync.apply(conn, [
            {"t": "requests", "k": json.dumps(["from-the-cloud"]), "d": theirs},
            {"t": "requests", "k": json.dumps([mine["id"]]), "d": {**row, "name": "Mine, renamed there", "a_column_from_the_future": 1}},
            {"t": "no_such_table", "k": "[1]", "d": {"x": 1}},
        ])
        assert sorted(r["name"] for r in db.list_requests()) == ["Mine, renamed there", "Theirs"]
        assert cloudsync.take(conn)[1] == {}                    # nothing to send back

        cloudsync.apply(conn, [{"t": "requests", "k": json.dumps(["from-the-cloud"]), "d": None}])
        assert [r["name"] for r in db.list_requests()] == ["Mine, renamed there"]
        db.update_request(mine["id"], {**db.get_request(mine["id"]), "name": "Edited here"})
        assert len(cloudsync.take(conn)[1]) == 1                # an edit made here still counts


def test_secret_values_stay_here_and_are_not_overwritten_by_empty_ones() -> None:
    row = {"id": "e1", "name": "Prod", "variables": json.dumps([
        {"key": "host", "value": "prod.example"}, {"key": "api_key", "value": "sk-live", "secret": True}])}
    up = cloudsync.without_secrets("environments", row)
    assert json.loads(up["variables"]) == [{"key": "host", "value": "prod.example"}, {"key": "api_key", "value": "", "secret": True}]
    assert cloudsync.without_secrets("requests", row) is row and cloudsync.without_secrets("environments", None) is None

    arriving = {**up, "variables": json.dumps([{"key": "host", "value": "new.example"}, {"key": "api_key", "value": "", "secret": True},
                                               {"key": "other", "value": "", "secret": True}])}
    kept = json.loads(cloudsync.keeping_local_secrets("environments", arriving, row)["variables"])
    assert kept == [{"key": "host", "value": "new.example"}, {"key": "api_key", "value": "sk-live", "secret": True}, {"key": "other", "value": "", "secret": True}]
    sent = {**up, "variables": json.dumps([{"key": "api_key", "value": "rotated", "secret": True}])}   # a value that does arrive wins
    assert json.loads(cloudsync.keeping_local_secrets("environments", sent, row)["variables"])[0]["value"] == "rotated"


def test_bytes_survive_the_trip_and_unlinking_leaves_nothing_behind(tmp_path: Path) -> None:
    row = {"id": "h1", "body": b"\x00\xffbinary", "n": 3, "text": "plain"}
    assert cloudsync.from_wire(json.loads(json.dumps(cloudsync.to_wire(row)))) == row

    database, conn = project(tmp_path)
    with db.using(database):
        cloudsync.install(conn)
        cloudsync.set_meta(conn, project="p1")
        db.create_request({"name": "Kept", "method": "GET", "url": "https://x"})
        cloudsync.uninstall(conn)
        assert not cloudsync.linked(database.path)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '\\_cloud\\_%' ESCAPE '\\'").fetchone()[0] == 0
        db.create_request({"name": "After unlinking", "method": "GET", "url": "https://y"})   # no trigger is left to trip over
        assert sorted(r["name"] for r in db.list_requests()) == ["After unlinking", "Kept"]


def test_an_app_that_never_signed_in_does_nothing(tmp_path: Path) -> None:
    syncer = cloudsync.Syncer(tmp_path, lambda: tmp_path / "pyhttp.db")
    syncer.touch()
    syncer.start()
    assert syncer._timer is None and syncer._poller is None and not (tmp_path / "cloud.json").exists()
    assert syncer.status()["signed_in"] is False
