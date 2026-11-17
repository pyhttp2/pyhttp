"""Things that must stay bounded however they are asked for: a text with {{variables}},
an imported spec, a script that never ends.

Run: python -m pytest tests/test_bounds.py -q
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp import openapi, templating  # noqa: E402
from tests.test_backend import client, echo_url  # noqa: E402, F401 - fixtures


def test_a_rendered_text_has_an_end(monkeypatch):
    assert len(templating.render("{{$randomString 99999999999}}", {"vars": {}})) == templating.MAX_RANDOM_STRING
    assert len(templating.render("{{$randomString 16}}", {"vars": {}})) == 16
    monkeypatch.setattr(templating, "MAX_OUTPUT", 10_000)
    ctx = {"vars": {"v": "x" * 1000}}
    assert len(templating.render("{{v}}" * 9, ctx)) == 9000
    with pytest.raises(ValueError, match="grows past"):
        templating.render("{{v}}" * 20, ctx)
    assert templating.render("no variables " * 2000, ctx).startswith("no variables")   # only what is filled in counts against it


def test_a_spec_unfolds_only_so_far():
    # nine YAML aliases nine deep: a few hundred bytes, hundreds of millions of values written out
    bomb = "a: &a [x,x,x,x,x,x,x,x,x]\n" + "".join(
        f"{chr(98 + n)}: &{chr(98 + n)} [{','.join(['*' + chr(97 + n)] * 9)}]\n" for n in range(8))
    began = time.monotonic()
    with pytest.raises(ValueError, match="too large once its YAML aliases"):
        openapi.parse_text(bomb)
    assert time.monotonic() - began < 10
    assert openapi.parse_text("a: &x {k: 1}\nb: *x\n") == {"a": {"k": 1}, "b": {"k": 1}}          # ordinary aliases are fine

    # schemas that each have several properties of the next one: the example stops at its budget
    schemas = {f"S{n}": {"type": "object", "properties": {f"p{k}": {"$ref": f"#/components/schemas/S{n + 1}"} for k in range(6)}}
               for n in range(8)}
    schemas["S8"] = {"type": "string"}
    spec = openapi._Spec({"components": {"schemas": schemas}})
    began = time.monotonic()
    example = spec.example({"$ref": "#/components/schemas/S0"})
    assert time.monotonic() - began < 5 and openapi._nodes(example, 10**7) <= openapi.MAX_EXAMPLE_NODES * 2
    small = openapi._Spec({}).example({"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}})
    assert set(small) == {"id", "name"}


def test_a_script_that_spins_does_not_stop_the_app_answering(client, echo_url, monkeypatch):  # noqa: F811
    """Scripts run beside the event loop: while one burns its time, other calls are answered."""
    import threading

    from pyhttp import jsengine

    monkeypatch.setattr(jsengine, "SCRIPT_TIMEOUT", 2.0)
    real = jsengine.run
    monkeypatch.setattr(jsengine, "run", lambda code, state, *, event, timeout=2.0: real(code, state, event=event, timeout=2.0))
    answered: list[float] = []

    def ask() -> None:
        time.sleep(0.5)
        began = time.monotonic()
        assert client.get("/api/tags").status_code == 200
        answered.append(time.monotonic() - began)

    other = threading.Thread(target=ask)
    other.start()
    result = client.post("/api/send", json={"name": "spin", "method": "GET", "url": echo_url + "/echo",
                                           "options": {"scripts": {"lang": "js", "pre": "while (true) {}"}}}).json()
    other.join()
    assert result["status"] == "error" and "script" in result["error"].lower()
    assert answered and answered[0] < 1.5, answered      # answered while the script was still spinning
