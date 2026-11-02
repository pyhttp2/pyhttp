"""The MCP server's tools (python -m pyhttp mcp), called directly: each one wraps the same
in-process REST API the UI uses, so these tests cover the whole path down to the database."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp import db  # noqa: E402
from pyhttp import mcp as m  # noqa: E402
from tests.test_backend import echo_url  # noqa: E402,F401 (session fixture)


@pytest.fixture()
def mcp_env():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        m.configure(Path(tmp))
        yield
        if m._client is not None:
            asyncio.run(m._client.aclose())
            m._client = None
        db.close()


def test_request_crud_and_folders(mcp_env):
    async def scenario():
        await m.create_folder("API")
        made = await m.create_request(
            "Users", "https://api.example.com/users", method="post", folder="API",
            headers=[{"key": "X-One", "value": "1", "enabled": True}],
            body_type="json", body='{"n": 1}', tags=["smoke"],
        )
        assert made["method"] == "POST" and made["bodies"] == ["Body 1"]
        folders = await m.list_folders()
        assert [f["name"] for f in folders] == ["API"]
        assert made["folder_id"] == folders[0]["id"]

        rows = await m.list_requests(query="users")
        assert len(rows) == 1 and rows[0]["tags"] == ["smoke"]
        full = await m.get_request(made["id"])
        assert full["headers"][0]["key"] == "X-One"

        await m.update_request(made["id"], {"url": "https://api.example.com/v2/users", "tags": ["smoke", "v2"]})
        full = await m.get_request(made["id"])
        assert full["url"].endswith("/v2/users") and full["tags"] == ["smoke", "v2"]

        # a folder given by name that does not exist is created for create_request, but not for run()
        await m.create_request("Other", "https://x.example", folder="New folder")
        assert {f["name"] for f in await m.list_folders()} == {"API", "New folder"}

        await m.delete_request(made["id"])
        assert all(r["id"] != made["id"] for r in await m.list_requests())

    asyncio.run(scenario())


def test_send_and_history(mcp_env, echo_url):
    async def scenario():
        # ad-hoc send, recorded in history
        res = await m.send(echo_url + "/echo", method="POST", headers=[{"key": "X-T", "value": "9"}],
                           body='{"a": 1}')
        assert res["status_code"] == 200
        assert '"x-t": "9"' in res["body"].lower() or '"x-t":"9"' in res["body"].lower()

        # saved request with two bodies: body_name picks which one is sent
        made = await m.create_request("Two bodies", echo_url + "/echo", method="POST", body_type="json", body="")
        await m.update_request(made["id"], {"bodies": [
            {"name": "first", "body": '{"which": "first"}', "enabled": True},
            {"name": "second", "body": '{"which": "second"}', "enabled": True},
        ]})
        res = await m.send_request(made["id"], body_name="second")
        assert res["status_code"] == 200 and '\\"which\\": \\"second\\"' in res["body"]
        assert res["body_name"] == "second"
        with pytest.raises(RuntimeError, match="No body named"):
            await m.send_request(made["id"], body_name="third")

        hist = await m.get_history(limit=5)
        assert hist and hist[0]["status_code"] == 200
        entry = await m.get_history_entry(hist[0]["id"])
        assert entry.get("response") or entry.get("status_code") == 200

    asyncio.run(scenario())


def test_run_variables_and_curl(mcp_env, echo_url):
    async def scenario():
        await m.set_global_variable("base", echo_url)
        eff = await m.get_variables()
        assert eff["variables"]["base"] == echo_url

        a = await m.create_request("A", "{{base}}/echo",
                                   tests=[{"type": "status", "op": "equals", "value": "200", "enabled": True}])
        await m.create_request("B", "{{base}}/echo", tags=["pair"])
        report = await m.run(request_ids=[a["id"]], tag="pair", name="mcp run")
        assert report["status"] == "completed" and report["total"] == 2 and report["failed"] == 0
        assert all(r["status_code"] == 200 for r in report["results"])
        assert report["results"][0]["tests_failed"] == 0

        envs = await m.list_environments()
        assert envs["active"] is None if "active" in envs else True
        imported = await m.import_curl(f"curl -X POST {echo_url}/echo -H 'X-C: 7' -d 'hello'", folder="Imported")
        assert imported["method"] == "POST"
        full = await m.get_request(imported["id"])
        assert any(h["key"].lower() == "x-c" for h in full["headers"])

    asyncio.run(scenario())
