"""UI checks in a real (headless) browser via Playwright. Skipped when Playwright or a
Chromium-based browser is not available: `pip install playwright` (uses the system
Edge/Chrome, or `python -m playwright install chromium`).

Run: python -m pytest tests/test_ui.py -q
"""
from __future__ import annotations

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyhttp.app import create_app  # noqa: E402
from tests.test_backend import echo  # noqa: E402

pw = pytest.importorskip("playwright.sync_api")


def _serve(app, name: str):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True, name=name).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def servers():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    app = create_app(Path(tmp.name))
    s1, app_url = _serve(app, "pyhttp")
    s2, echo_url = _serve(echo, "echo")
    yield app_url, echo_url
    s1.should_exit = True
    s2.should_exit = True
    from pyhttp import db
    db.close()
    tmp.cleanup()


@pytest.fixture(scope="module")
def browser():
    """Chromium by default. PYHTTP_UI_BROWSER=webkit runs the suite in WebKit — the engine the macOS
    app window uses, and the one that behaves differently on scroll restoration and rebuilt lists."""
    engine = os.environ.get("PYHTTP_UI_BROWSER", "chromium").lower()
    with pw.sync_playwright() as p:
        b = None
        if engine in ("webkit", "firefox"):
            try:
                b = getattr(p, engine).launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"{engine} is not installed for Playwright ({exc}); run: python -m playwright install {engine}")
        else:
            for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
                try:
                    b = p.chromium.launch(headless=True, **kw)
                    break
                except Exception:  # noqa: BLE001
                    continue
        if b is None:
            pytest.skip("no Chromium-based browser available for Playwright")
        yield b
        b.close()


@pytest.fixture()
def page(browser, servers):
    app_url, _ = servers
    ctx = browser.new_context(viewport={"width": 1500, "height": 950})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.errors = errors  # type: ignore[attr-defined]
    page.goto(app_url)
    page.wait_for_selector("#btn-new")
    yield page
    assert not errors, errors
    ctx.close()


def new_request(page, echo_url: str, name: str = "UI test"):
    page.click("#btn-new")
    page.fill("#f-name", name)
    page.fill("#f-url", echo_url + "/echo")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")


def set_editor(page, selector: str, text: str):
    """Set the content of a CodeMirror-backed textarea (the textarea itself is hidden)."""
    page.evaluate("([sel, text]) => document.querySelector(sel)._enhanced.setValue(text)", [selector, text])


def editor_cursor(page, selector: str, anchor: int, head: int | None = None):
    page.evaluate("([sel, a, h]) => document.querySelector(sel)._enhanced.setCursor(a, h)", [selector, anchor, head])


def test_sidebar_ignores_a_late_answer_to_an_older_search(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "race alpha")
    new_request(page, echo_url, "race beta")
    total = page.locator("#req-list .req-item").count()
    assert total >= 2

    # the filtered query answers slowly, so it arrives after the unfiltered one that follows it
    def slow(route):
        time.sleep(0.8)
        route.continue_()
    page.route("**/api/requests?q=race+alpha*", slow)
    page.fill("#search", "race alpha")
    page.wait_for_timeout(250)          # past the search debounce: the filtered request is out
    page.fill("#search", "")
    page.wait_for_timeout(1500)         # both answers are in
    page.unroute("**/api/requests?q=race+alpha*")
    assert page.locator("#req-list .req-item").count() == total


def test_editor_comment_toggle_covers_folded_block(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url)
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#btn-body-add")
    page.fill("#body-name", "folded")
    ta = page.locator("#f-body")
    text = '{\n  "a": {\n    "x": 1,\n    "y": 2\n  },\n  "b": 2\n}'
    set_editor(page, "#f-body", text)
    # fold the "a" object (second fold arrow), then Ctrl+/ with the cursor on its visible line
    page.wait_for_function("document.querySelectorAll('#body-text-wrap .cm-foldGutter span[title=\"Fold line\"]').length >= 2")
    page.click("#body-text-wrap .cm-foldGutter span[title=\"Fold line\"] >> nth=1")
    page.wait_for_selector("#body-text-wrap .cm-foldPlaceholder")
    editor_cursor(page, "#f-body", 4)
    page.keyboard.press("Control+/")
    assert ta.input_value() == '{\n  // "a": {\n  //   "x": 1,\n  //   "y": 2\n  // },\n  "b": 2\n}'
    assert page.locator("#body-text-wrap .cm-foldPlaceholder").count() == 1  # still folded
    # toggling again uncomments the whole block; the unfolded "b" line is untouched throughout
    page.keyboard.press("Control+/")
    assert ta.input_value() == text
    # a line that is not folded still toggles on its own
    editor_cursor(page, "#f-body", text.index('"b"'))
    page.keyboard.press("Control+/")
    assert ta.input_value() == text.replace('  "b": 2', '  // "b": 2')


def test_editor_comment_toggle_and_json_status(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url)
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#btn-body-add")
    page.fill("#body-name", "valid")
    ta = page.locator("#f-body")
    set_editor(page, "#f-body", '{\n  "a": 1,\n  "b": 2\n}')
    # CodeMirror: line numbers and a fold arrow on the object line
    assert page.locator("#body-text-wrap .cm-lineNumbers .cm-gutterElement").count() >= 4
    page.wait_for_selector("#body-text-wrap .cm-foldGutter span[title=\"Fold line\"]")
    page.wait_for_selector("#body-text-wrap .code-status.ok")
    # Ctrl+/ on line 3 comments it out and the linter reports the trailing comma problem
    editor_cursor(page, "#f-body", 14)
    page.keyboard.press("Control+/")
    page.wait_for_function("document.querySelector('#f-body').value === '{\\n  \"a\": 1,\\n  // \"b\": 2\\n}'")
    page.wait_for_selector("#body-text-wrap .code-status.err")
    assert "Trailing comma" in page.locator("#body-text-wrap .code-status").text_content()
    assert page.locator("#body-text-wrap .cm-lintRange-error").count() >= 1
    # toggle back, then comment a multi-line selection
    page.keyboard.press("Control+/")
    assert ta.input_value() == '{\n  "a": 1,\n  "b": 2\n}'
    editor_cursor(page, "#f-body", 4, 20)
    page.keyboard.press("Control+/")
    assert ta.input_value() == '{\n  // "a": 1,\n  // "b": 2\n}'
    page.keyboard.press("Control+/")
    # Tab indents the selected lines, Shift+Tab outdents
    page.keyboard.press("Tab")
    assert ta.input_value() == '{\n    "a": 1,\n    "b": 2\n}'
    page.keyboard.press("Shift+Tab")
    assert ta.input_value() == '{\n  "a": 1,\n  "b": 2\n}'
    # Enter keeps the indentation and brackets close themselves
    editor_cursor(page, "#f-body", 11)
    page.keyboard.press("Enter")
    page.keyboard.type('"c": {')
    assert ta.input_value() == '{\n  "a": 1,\n  "c": {}\n  "b": 2\n}'
    # folding: fold the root object, the placeholder appears, unfold restores
    page.click("#body-text-wrap .cm-foldGutter span[title=\"Fold line\"] >> nth=0")
    page.wait_for_selector("#body-text-wrap .cm-foldPlaceholder")
    page.click("#body-text-wrap .cm-foldPlaceholder")
    page.wait_for_function("!document.querySelector('#body-text-wrap .cm-foldPlaceholder')")
    # beautify keeps comments
    set_editor(page, "#f-body", '{"a":1, // one\n"b":[1,2]}')
    page.click("#btn-format-json")
    assert ta.input_value() == '{\n  "a": 1, // one\n  "b": [\n    1,\n    2\n  ]\n}\n'
    page.wait_for_selector("#body-text-wrap .code-status.ok")
    assert "comments are removed" in page.locator("#body-text-wrap .code-status").text_content()
    # Ctrl+Enter inside the editor sends the request
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("#response:not([hidden]) .jv")


def test_send_renders_json_tree_tests_and_history(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Send test")
    page.select_option("#f-method", "POST")
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#btn-body-add")
    set_editor(page, "#f-body", '{\n  "user": "ann", // name\n  "tags": ["a", "b"]\n}')
    # a test and an extraction
    page.click("#editor-tabs button[data-tab='tests']")
    page.click("#btn-test-add")
    page.click("[data-preset='3']")  # body is JSON
    page.click("#btn-extract-add")
    page.fill("#extract-list .x-name", "echoed_method")
    page.fill("#extract-list .x-path", "method")
    page.select_option("#extract-list .x-scope", "global")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    assert page.locator("#n-tests").text_content() == "3"
    page.click("#btn-send")
    page.wait_for_selector("#response:not([hidden]) .jv")
    assert "200" in page.locator("#r-status").text_content()
    # the tree shows coloured keys and the comment was stripped from the echoed body
    assert page.locator("#r-body .jv-k").count() > 5
    body_line = page.locator("#r-body .jv-node[data-path='body'] .jv-s").text_content()
    assert "// name" not in body_line and '\\"user\\": \\"ann\\"' in body_line
    # raw / pretty switch, wrap toggle, filter and search
    page.click("#r-body .rv-views button[data-view='raw']")
    assert page.locator("#r-body .rv-pre").count() == 1
    page.click("#r-body .rv-views button[data-view='pretty']")
    page.fill("#r-body .rv-filter", "headers.content-type")
    page.wait_for_function("document.querySelector('#r-body .jv') && document.querySelector('#r-body .jv').textContent.includes('application/json')")
    page.fill("#r-body .rv-filter", "")
    page.fill("#r-body .rv-search", "ann")
    page.wait_for_selector("#r-body mark.rv-m.cur")
    assert page.locator("#r-body .rv-count").text_content().startswith("1/")
    # tests tab
    assert page.locator("#r-tests-badge").text_content() == "2 tests passed"
    page.click("#resp-tabs button[data-tab='tests']")
    assert page.locator("#r-tests li.pass").count() == 3  # 2 tests + 1 extraction
    assert "echoed_method = POST" in page.locator("#r-tests").text_content()
    # headers and request tabs
    page.click("#resp-tabs button[data-tab='headers']")
    assert page.locator("#r-headers .htable tr").count() > 2
    page.click("#resp-tabs button[data-tab='request']")
    assert "content-type" in page.locator("#r-request").text_content().lower()
    # history sidebar lists the send
    page.click("#runs-tabs button[data-tab='requests']")
    page.wait_for_selector("#history-list .h-item")
    assert "Send test" in page.locator("#history-list .h-item").first.text_content()
    # code snippet uses the resolved request
    page.keyboard.press("Control+Shift+C")
    page.wait_for_selector("#snip-out")
    assert "curl -X POST" in page.locator("#snip-out").text_content()
    page.select_option("#snip-lang", "python")
    assert "import requests" in page.locator("#snip-out").text_content()
    page.click("#modal-close")


def test_auth_tab_and_unresolved_warning(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Auth test")
    page.click("#editor-tabs button[data-tab='auth']")
    page.select_option("#auth-type", "bearer")
    page.fill("#auth-token", "{{token}}")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    # unresolved {{token}} asks first
    page.wait_for_selector(".dialog")
    assert "token" in page.locator(".dialog-msg").text_content()
    page.click(".dialog-foot button.primary")
    page.wait_for_selector("#response:not([hidden]) .jv")
    page.click("#resp-tabs button[data-tab='request']")
    assert "{{token}}" in page.locator("#r-request").text_content()
    assert "Unresolved variables" in page.locator("#r-request").text_content()


def test_body_modes_and_import_curl(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Form test")
    page.select_option("#f-method", "POST")
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#btn-body-add")
    page.select_option("#f-body-type", "form")
    page.wait_for_selector("#body-form-wrap:not([hidden])")
    page.click("#kv-form .link")  # add row
    page.fill("#kv-form .kv-key", "name")
    page.fill("#kv-form .kv-val", "Ann Lee")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#response:not([hidden]) .jv")
    assert "name=Ann+Lee" in page.locator("#r-body .jv-node[data-path='body']").text_content()
    # GraphQL mode shows two editors and stores JSON
    page.select_option("#f-body-type", "graphql")
    page.wait_for_selector("#body-graphql-wrap:not([hidden])")
    set_editor(page, "#f-gql-query", "{ me { id } }")
    set_editor(page, "#f-gql-vars", '{"a": 1}')
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#r-body .jv') && document.querySelector('#r-body .jv').textContent.includes('me { id }')")
    # cURL import creates and opens a request
    page.click("#btn-import")
    page.click("#imp-paste")
    page.fill("#imp-text", f"curl -X PUT '{echo_url}/echo?x=1' -H 'X-Test: yes' -d '{{\"k\": 2}}'")
    page.click("#modal-foot button.primary")
    page.wait_for_function("document.querySelector('#f-method').value === 'PUT'")
    assert page.locator("#f-url").input_value() == echo_url + "/echo"
    assert page.locator("#n-params").text_content() == "1"
    assert page.locator("#n-headers").text_content() == "1"
    assert page.locator("#req-tabs .req-tab").count() >= 2


def test_tabs_cookies_settings_and_help(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Tab A")
    new_request(page, echo_url, "Tab B")
    tabs = page.locator("#req-tabs .req-tab")
    assert tabs.count() == 2 and "Tab B" in tabs.nth(1).text_content()
    # drag the second tab before the first, then back
    tabs.nth(1).drag_to(tabs.nth(0), target_position={"x": 5, "y": 10})
    page.wait_for_function("document.querySelector('#req-tabs .req-tab').textContent.includes('Tab B')")
    page.locator("#req-tabs .req-tab").nth(0).drag_to(page.locator("#req-tabs .req-tab").nth(1), target_position={"x": 90, "y": 10})
    page.wait_for_function("document.querySelector('#req-tabs .req-tab').textContent.includes('Tab A')")
    # runs panel can be hidden and shown again
    page.click("#runs-close")
    page.wait_for_function("document.querySelector('.layout').classList.contains('no-runs')")
    page.click("#sb-runs")
    page.wait_for_function("!document.querySelector('.layout').classList.contains('no-runs')")
    tabs.nth(0).click()
    page.wait_for_function("document.querySelector('#f-name').value === 'Tab A'")
    page.keyboard.press("Control+w")
    page.wait_for_function("document.querySelector('#f-name').value === 'Tab B'")
    assert page.locator("#req-tabs .req-tab").count() == 1
    # cookie jar: a response sets one, the manager lists it
    page.fill("#f-url", echo_url + "/set-cookie")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#response:not([hidden])")
    page.click("#resp-tabs button[data-tab='cookies']")
    assert "session" in page.locator("#r-cookies").text_content()
    # response can be collapsed (request fills the view) and maximized (request hidden); Esc restores
    page.click("#r-collapse")
    assert page.locator("#response.collapsed").count() == 1 and not page.locator("#resp-panels").is_visible()
    page.click("#r-collapse")
    assert page.locator("#resp-panels").is_visible()
    page.click("#r-max")
    page.wait_for_function("document.querySelector('#editor-form').classList.contains('resp-max')")
    assert not page.locator("#f-url").is_visible()
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#editor-form').classList.contains('resp-max')")
    assert page.locator("#f-url").is_visible()
    page.click("#btn-cookies")
    page.wait_for_selector("#cookie-box .cookie-domain")
    assert "abc123" in page.locator("#cookie-box input.ck-val").input_value()
    page.click("#modal-foot button.primary")
    # settings dialog with the new tabs
    page.click("#btn-settings")
    page.click("#set-tabs button[data-tab='network']")
    page.fill("#set-proxy-bypass", "localhost, 127.0.0.1, *.internal")
    page.press("#set-proxy-bypass", "Enter")
    # the settings card keeps its height across tabs; "Check now" reports inline
    h_network = page.locator("#set-panels").bounding_box()["height"]
    page.click("#set-tabs button[data-tab='about']")
    assert page.locator("#set-panels").bounding_box()["height"] == h_network
    page.click("#set-update-now")
    page.wait_for_function("document.querySelector('#set-update-status').textContent.trim().length > 0", timeout=20000)
    assert "Check now" in page.locator("#set-update-now").text_content()
    page.click("#set-tabs button[data-tab='general']")
    page.select_option("#set-layout", "beside")
    page.wait_for_function("document.querySelector('#editor-section').classList.contains('beside')")
    page.select_option("#set-layout", "below")
    # fonts: Inter is the bundled default; a pick applies at once and survives a reload
    assert page.evaluate("document.fonts.check('14px \"Inter Variable\"')")
    assert "Inter" in page.evaluate("getComputedStyle(document.body).fontFamily")
    page.select_option("#set-uifont", "verdana")
    page.select_option("#set-monofont", "consolas")
    page.wait_for_function("getComputedStyle(document.body).fontFamily.startsWith('Verdana')")
    page.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--mono').trim().startsWith('Consolas')")
    page.select_option("#set-uifont", "inter")
    page.select_option("#set-monofont", "default")
    page.wait_for_function("getComputedStyle(document.body).fontFamily.includes('Inter')")
    page.click("#modal-foot button.primary")
    # checkboxes are the larger custom box everywhere, sidebar included
    box = page.locator("#req-list .req-item input[type=checkbox]").first.bounding_box()
    assert box["width"] >= 18 and box["height"] >= 18
    # glyph buttons are sized to match: the tab ✕ and the sidebar ⋯ are real hit targets
    x = page.locator("#req-tabs .req-tab .rt-close").first.bounding_box()
    assert x["width"] >= 20 and x["height"] >= 20, x
    more = page.locator("#req-list .req-item .more").first.bounding_box()
    assert more["width"] >= 26 and more["height"] >= 26, more
    # help
    page.keyboard.press("?")
    page.wait_for_selector(".htable.keys")
    page.click("#modal-foot button.primary")
    # run with tests shows counts in the run panel
    page.click("#editor-tabs button[data-tab='tests']")
    page.click("[data-preset='0']")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.check("#req-list .req-item:has-text(\"Tab B\") input[type=checkbox]")
    page.click("#btn-run")
    page.fill("#run-iterations", "2")
    page.click("#modal-foot button.accent")
    page.wait_for_function("document.querySelector('#run-current .badge.completed') !== null", timeout=15000)
    txt = page.locator("#run-current").text_content()
    assert "2 iterations" in txt and "tests" in txt
    assert page.locator("#run-current .result-item .tcount.pass").count() == 2
    page.click("#btn-report-html")  # browser download: just must not error


def test_send_becomes_cancel(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Slow")
    page.fill("#f-url", echo_url + "/slow?seconds=8")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#btn-send').classList.contains('cancel')")
    assert "Cancel" in page.locator("#btn-send").text_content()
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#r-status').textContent === 'Cancelled'", timeout=6000)
    page.wait_for_function("!document.querySelector('#btn-send').classList.contains('cancel')")
    assert page.locator("#btn-send").text_content() == "Send"


def test_selected_body_and_checks_persist(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Bodies")
    page.click("#editor-tabs button[data-tab='body']")
    for i in range(3):
        page.click("#btn-body-add")
        page.fill("#body-name", f"body{i}")
        page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.locator("#body-list .body-item input").nth(0).uncheck()
    page.locator("#body-list .body-item").nth(1).click()
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    state = "Array.from(document.querySelectorAll('#body-list .body-item')).map(li => [li.querySelector('input').checked, li.classList.contains('active')]).join()"
    expected = "false,false,true,true,true,false"
    assert page.evaluate(state) == expected
    # another request, then back through the tab
    new_request(page, echo_url, "Other")
    page.click("#req-tabs .req-tab >> nth=0")
    page.wait_for_function("document.querySelector('#f-name').value === 'Bodies'")
    page.click("#editor-tabs button[data-tab='body']")
    assert page.evaluate(state) == expected
    # and after a full reload (app closed and opened)
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Bodies')")
    page.wait_for_function("document.querySelector('#f-name').value === 'Bodies'")
    page.click("#editor-tabs button[data-tab='body']")
    assert page.evaluate(state) == expected
    # toggling checkboxes alone (no other edit) must also survive a tab switch
    page.locator("#body-list .body-item input").nth(0).check()
    page.locator("#body-list .body-item input").nth(1).uncheck()
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    expected = "true,false,false,true,true,false"
    assert page.evaluate(state) == expected
    page.click("#req-list .req-item:has-text('Other')")
    page.wait_for_function("document.querySelector('#f-name').value === 'Other'")
    page.click("#req-tabs .req-tab:has-text('Bodies')")
    page.wait_for_function("document.querySelector('#f-name').value === 'Bodies'")
    page.click("#editor-tabs button[data-tab='body']")
    assert page.evaluate(state) == expected


def test_scripts_console_run_modal_folder_settings_and_reorder(page, servers):
    app_url, echo_url = servers
    # folder with inherited header, request inside it
    page.click("#btn-new-folder")
    page.fill("#dlg-in", "Shop")
    page.click(".dialog-foot button.primary")
    page.wait_for_selector(".folder-item")
    page.click(".folder-item .more")
    page.click("#ctx-menu button[data-act='settings']")
    page.wait_for_selector("#fs-headers")
    page.click("#fs-headers .link")
    page.fill("#fs-headers .kv-key", "X-Folder")
    page.fill("#fs-headers .kv-val", "f1")
    page.select_option("#fs-auth-type", "apikey")
    page.fill("#auth-key", "X-Key")
    page.fill("#auth-value", "k")
    page.click("#modal-foot button.primary")
    page.wait_for_selector("#modal", state="hidden")
    page.click(".folder-item .more")
    page.click("#ctx-menu button[data-act='add-req']")
    page.fill("#f-name", "In folder")
    page.fill("#f-url", echo_url + "/echo")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#editor-tabs button[data-tab='headers']")
    page.wait_for_selector("#inherited-headers:not([hidden])")
    assert "X-Folder" in page.locator("#inherited-headers").text_content()
    page.click("#editor-tabs button[data-tab='auth']")
    assert "Inherits" in page.locator("#auth-panel #auth-fields").text_content()
    # JavaScript (Postman pm) test script, the default language
    page.click("#editor-tabs button[data-tab='tests']")
    assert page.locator("#f-script-lang").input_value() == "js"
    set_editor(page, "#f-script-test", 'pm.test("folder header echoed", function () {\n  pm.expect(pm.response.json().headers["x-folder"]).to.eql("f1");\n});\nconsole.log("hello log");')
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#response:not([hidden]) .jv")
    page.click("#resp-tabs button[data-tab='tests']")
    assert page.locator("#r-tests li.pass").count() == 1
    assert "hello log" in page.locator("#r-tests .script-log").text_content()
    # switch to Python: placeholders and reference change, script still runs
    page.select_option("#f-script-lang", "py")
    assert page.locator("#f-script-test").get_attribute("placeholder").startswith("py.test")
    set_editor(page, "#f-script-test", 'py.test("python works", py.response.status_code == 200)')
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#r-tests') && document.querySelector('#r-tests').textContent.includes('python works')")
    page.select_option("#f-script-lang", "js")
    set_editor(page, "#f-script-test", 'pm.test("folder header echoed", () => pm.response.to.have.header("content-type"));')
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    # console shows the entry with a mounted viewer
    page.keyboard.press("Control+Alt+c")
    page.wait_for_selector("#console:not([hidden]) .c-row")
    page.click("#console .c-row >> nth=-1")
    page.wait_for_selector("#console .c-viewer .jv")
    page.keyboard.press("Control+Alt+c")
    # run the folder, open a result: viewer mounted in the modal
    page.click(".folder-item .more")
    page.click("#ctx-menu button[data-act='run']")
    page.click("#modal-foot button.accent")
    page.wait_for_function("document.querySelector('#run-current .badge.completed') !== null", timeout=15000)
    page.click("#run-current .result-item[data-rid]")
    page.wait_for_selector("#res-viewer .jv")
    assert page.locator("#modal .test-results li.pass").count() == 1
    page.click("#modal-close")
    # second request at top level, then drag it before the first inside the folder
    new_request(page, echo_url, "Dragged")
    src = page.locator("#req-list .req-item:has-text('Dragged')")
    dst = page.locator("#req-list .req-item:has-text('In folder')")
    src.drag_to(dst, target_position={"x": 40, "y": 4})
    page.wait_for_function("Array.from(document.querySelectorAll('#req-list .req-item .name')).map(e => e.textContent).join('|').startsWith('Dragged|In folder')")
    rows = page.locator("#req-list .req-item")
    assert rows.nth(0).get_attribute("data-folder") == rows.nth(1).get_attribute("data-folder")
    # history "open as new request"
    page.click("#runs-tabs button[data-tab='requests']")
    page.wait_for_selector("#history-list .h-item")
    page.click("#history-list .h-item .more >> nth=0")
    page.click("#ctx-menu button[data-act='restore']")
    page.wait_for_function("document.querySelector('#f-name').value.endsWith('(from history)')")


def test_history_panel_oauth_secrets_resize_and_openapi(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Hist A")
    # request history lives in the right-hand panel, next to Current run / Run history
    assert page.locator("#side-tabs").count() == 0
    assert page.locator("#runs-tabs button").all_text_contents()[:3] == ["Current run", "Run history", "Request history"]
    page.click("#runs-tabs button[data-tab='requests']")
    count = "document.querySelectorAll('#history-list .h-item').length"
    page.wait_for_timeout(300)
    n0 = page.evaluate(count)
    page.click("#btn-send")
    page.wait_for_function(f"{count} === {n0 + 1}")  # refreshed after the send without reopening the tab
    page.fill("#f-url", echo_url + "/status/404")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_function(f"{count} === {n0 + 2}")
    # status filter chips
    page.click("#history-filters button[data-status='4xx']")
    page.wait_for_function(f"{count} < {n0 + 2}")
    codes = page.locator("#history-list .h-code").all_text_contents()
    assert codes and all(c.startswith("4") for c in codes)
    page.click("#history-filters button[data-status='']")
    page.wait_for_function(f"{count} === {n0 + 2}")
    # one-click send again adds an entry
    page.hover("#history-list .h-item >> nth=0")
    page.click("#history-list .h-item .h-resend >> nth=0")
    page.wait_for_function(f"{count} === {n0 + 3}")
    # compare two entries: the body diff shows both sides
    page.click("#history-list .h-item .more >> nth=0")
    page.click("#ctx-menu button[data-act='compare']")
    page.wait_for_selector("#history-compare:not([hidden])")
    page.click("#history-list .h-item >> nth=2")
    page.wait_for_selector("#modal .diff .d-add")
    assert page.locator("#modal .diff .d-del").count() > 0
    assert page.locator("#history-compare").is_hidden()
    page.click("#modal-close")

    # OAuth 2.0: get a token from the Auth tab, then the send uses it
    page.click("#editor-tabs button[data-tab='auth']")
    page.select_option("#auth-type", "oauth2")
    page.fill("#auth-token_url", echo_url + "/oauth/token")
    page.fill("#auth-client_id", "cid")
    page.fill("#auth-client_secret", "csecret")
    assert page.locator(".oa-pw").is_hidden()
    page.select_option("#auth-grant", "password")
    assert page.locator(".oa-pw").is_visible()
    page.select_option("#auth-grant", "client_credentials")
    page.click(".oa-get")
    page.wait_for_function("document.querySelector('.oa-status').textContent.includes('expires in')")
    page.fill("#f-url", echo_url + "/protected")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#r-status.ok")
    # the auth settings were saved with the request
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Hist A')")
    page.click("#editor-tabs button[data-tab='auth']")
    assert page.locator("#auth-type").input_value() == "oauth2" and page.locator("#auth-client_id").input_value() == "cid"

    # secret environment variable: masked input, flag saved
    page.click("#btn-envs")
    page.click("#modal [data-preset='Prod']")
    page.wait_for_selector("#kv-env .kv-row")
    row = page.locator("#kv-env .kv-row >> nth=0")
    row.locator(".kv-secret").click()
    assert row.locator(".kv-val").get_attribute("type") == "password"
    page.wait_for_function("document.querySelector('#env-save-state').textContent === 'Saved'")
    page.click("#modal-close")
    envs = page.evaluate("fetch('/api/environments').then(r => r.json())")
    prod = next(e for e in envs["environments"] if e["name"] == "Prod")
    assert prod["variables"][0].get("secret") is True

    # resize the side panel by dragging its edge; the width is remembered
    box = page.locator(".sidebar > .resizer").bounding_box()
    before = page.locator(".sidebar").bounding_box()["width"]
    page.mouse.move(box["x"] + 3, box["y"] + 200)
    page.mouse.down()
    page.mouse.move(box["x"] + 63, box["y"] + 200, steps=4)
    page.mouse.up()
    assert abs(page.locator(".sidebar").bounding_box()["width"] - (before + 60)) < 4
    assert page.evaluate("localStorage.getItem('pyhttp.size.side')") is not None
    page.dblclick(".sidebar > .resizer")
    assert abs(page.locator(".sidebar").bounding_box()["width"] - before) < 4

    # OpenAPI spec in YAML, pasted into Import
    page.click("#btn-import")
    page.click("#imp-paste")
    page.fill("#imp-text", "openapi: 3.0.0\ninfo: {title: YAML API}\nservers: [{url: 'https://y.example.com'}]\n"
                           "paths:\n  /things:\n    get: {summary: List things, tags: [things]}\n")
    page.click("#modal-foot button.primary")
    page.wait_for_selector(".dialog-backdrop")
    assert "YAML API" in page.locator(".dialog-backdrop").text_content()
    page.click(".dialog-backdrop button.primary")
    page.wait_for_selector("#req-list .folder-item:has-text('YAML API')")
    reqs = page.evaluate("fetch('/api/requests').then(r => r.json())")
    assert any(r["name"] == "List things" and r["url"] == "{{baseUrl}}/things" for r in reqs)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_layout_sidebar_and_scripts_tab(page, servers):
    """Split panes with the response in view, the sidebar toggle, wrapped errors, the Scripts tab with per-body tests."""
    app_url, echo_url = servers
    page.evaluate("""([app, echo]) => fetch(app + '/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        name: 'Scoped bodies', method: 'POST', url: echo + '/echo', body_type: 'json',
        bodies: [{id: 's1', name: 'first', body: '{}', enabled: true}, {id: 's2', name: 'second', body: '{}', enabled: false}]})})""", [app_url, echo_url])
    page.reload()
    page.wait_for_selector("#btn-new")
    # sidebar toggle: button and Ctrl+\
    page.click("#btn-sidebar")
    assert page.evaluate("document.querySelector('.layout').classList.contains('no-side')")
    assert not page.locator(".sidebar").is_visible()
    page.keyboard.press("Control+\\")
    assert page.locator(".sidebar").is_visible()
    # the Scripts tab (was Tests) with a body scope on each test
    page.click("#req-list .req-item:has-text('Scoped bodies')")
    page.wait_for_selector("#editor-form:not([hidden])")
    tab = page.locator("#editor-tabs button[data-tab='tests']")
    assert tab.text_content().startswith("Scripts")
    tab.click()
    assert page.locator("#tests-panel h4").all_text_contents() == ["Pre-request", "Post-response"]
    page.click("#btn-test-add")
    assert page.evaluate("document.querySelector('#tests-list').classList.contains('scoped')")
    assert page.locator("#tests-list .t-body option").all_text_contents() == ["All bodies", "first", "second"]
    page.select_option("#tests-list .t-body", "s2")
    page.wait_for_function("document.querySelector('#save-state').textContent.includes('Saved')", timeout=10000)
    saved = page.evaluate("fetch('/api/requests').then((r) => r.json()).then((l) => l.find((r) => r.name === 'Scoped bodies'))")
    assert saved["tests"][0]["body_id"] == "s2"
    # send: the response head is in view without scrolling (split panes), in both layouts
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#r-status').textContent.trim().startsWith('200')", timeout=20000)
    in_view = "(() => { const r = document.querySelector('#r-status').getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight; })()"
    assert page.evaluate(in_view)
    assert page.locator("#response .resizer").is_visible()
    page.click("#sb-layout button[data-layout='beside']")
    page.wait_for_function("document.querySelector('#editor-section').classList.contains('beside')")
    assert page.evaluate(in_view)
    page.click("#r-layout")
    page.wait_for_function("!document.querySelector('#editor-section').classList.contains('beside')")
    assert page.evaluate("document.querySelector('#sb-layout button[data-layout=\"below\"]').classList.contains('on')")
    # a long error wraps instead of adding a horizontal scrollbar
    page.fill("#f-url", "http://127.0.0.1:1/a/very/long/path/" + "segment/" * 40)
    page.click("#btn-send")
    page.wait_for_selector("#r-body pre.err-text", timeout=20000)
    assert page.evaluate("getComputedStyle(document.querySelector('#r-body pre.err-text')).whiteSpace") == "pre-wrap"
    assert page.evaluate("document.querySelector('#resp-panels').scrollWidth <= document.querySelector('#resp-panels').clientWidth + 1")
    assert not page.errors, page.errors


def test_body_response_tabs_stay_visible(browser, servers):
    """Two checked bodies: the per-body response tabs keep their height on a short window and switch responses."""
    app_url, echo_url = servers
    ctx = browser.new_context(viewport={"width": 1366, "height": 700})
    page = ctx.new_page()
    page.goto(app_url)
    page.wait_for_selector("#btn-new")
    page.evaluate("""([app, echo]) => fetch(app + '/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        name: 'Two bodies', method: 'POST', url: echo + '/echo', body_type: 'json',
        bodies: [{id: 't1', name: 'valid user', body: '{"a": 1}', enabled: true}, {id: 't2', name: 'missing email', body: '{"b": 2}', enabled: true}]})})""", [app_url, echo_url])
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Two bodies')")
    page.wait_for_selector("#editor-form:not([hidden])")
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#resp-bodies') && !document.querySelector('#resp-bodies').hidden && document.querySelectorAll('#resp-bodies .rb-tab .rb-code').length >= 2", timeout=30000)
    strip = page.locator("#resp-bodies").bounding_box()
    tab = page.locator("#resp-bodies .rb-tab").nth(1).bounding_box()
    assert strip["height"] >= tab["height"] - 1 and tab["height"] > 18, (strip, tab)
    page.click("#resp-bodies .rb-tab:has-text('missing email')")
    page.wait_for_function("document.querySelector('#resp-bodies .rb-tab.active').textContent.includes('missing email')")
    assert 'b\\": 2' in page.locator("#r-body").text_content()   # the echo returns the sent body as an escaped string
    page.click("#resp-bodies .rb-tab:has-text('valid user')")
    page.wait_for_function("document.querySelector('#resp-bodies .rb-tab.active').textContent.includes('valid user')")
    assert 'a\\": 1' in page.locator("#r-body").text_content()
    ctx.close()


def test_scripts_per_body(page, servers):
    """Each script editor has a scope picker: a script for all bodies, plus one per named body (runs after it)."""
    app_url, echo_url = servers
    page.evaluate("""([app, echo]) => fetch(app + '/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        name: 'Per-body scripts', method: 'POST', url: echo + '/echo', body_type: 'json',
        bodies: [{id: 'p1', name: 'alpha', body: '{}', enabled: true}, {id: 'p2', name: 'beta', body: '{}', enabled: true}]})})""", [app_url, echo_url])
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Per-body scripts')")
    page.wait_for_selector("#editor-form:not([hidden])")
    page.click("#editor-tabs button[data-tab='tests']")

    def type_script(sel, text):
        set_editor(page, sel, text)
        page.evaluate("(sel) => document.querySelector(sel).dispatchEvent(new Event('input', {bubbles: true}))", sel)

    def options(kind):
        return page.locator(f".script-scope[data-kind='{kind}'] option").all_text_contents()

    def shown(kind):
        return page.evaluate("(sel) => document.querySelector(sel)._enhanced.view.state.doc.toString()", f"#f-script-{kind}")

    pre_scope, test_scope = ".script-scope[data-kind='pre']", ".script-scope[data-kind='test']"
    assert page.locator(pre_scope).is_visible() and page.locator(test_scope).is_visible()
    assert options("pre") == ["All bodies", "alpha", "beta"]
    # all-bodies pre-request script, then beta's own (empty editor when switching to it)
    type_script("#f-script-pre", "pm.request.headers.add({ key: 'X-All', value: '1' });")
    page.select_option(pre_scope, "p2")
    assert shown("pre") == ""
    type_script("#f-script-pre", "pm.request.headers.add({ key: 'X-Own', value: pm.variables.get('body_name') });")
    assert options("pre") == ["All bodies ●", "alpha", "beta ●"]
    assert "beta" in page.locator(".script-scope-hint[data-kind='pre']").text_content()
    # a test script for alpha only
    page.select_option(test_scope, "p1")
    type_script("#f-script-test", "pm.test('alpha only', () => pm.response.to.have.status(200));")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    saved = page.evaluate("fetch('/api/requests').then((r) => r.json()).then((l) => l.find((r) => r.name === 'Per-body scripts'))")
    sc = saved["options"]["scripts"]
    assert sc["pre"].startswith("pm.request.headers.add({ key: 'X-All'") and sc["test"] == ""
    assert set(sc["bodies"]) == {"p1", "p2"} and sc["bodies"]["p2"]["pre"].startswith("pm.request.headers.add({ key: 'X-Own'") and sc["bodies"]["p2"]["test"] == ""
    assert sc["bodies"]["p1"]["pre"] == "" and "alpha only" in sc["bodies"]["p1"]["test"]
    # switching back shows the all-bodies script again
    page.select_option(pre_scope, "")
    assert shown("pre").startswith("pm.request.headers.add({ key: 'X-All'")
    # send beta alone: the all-bodies pre-request runs, then beta's own; alpha's test does not
    page.click("#editor-tabs button[data-tab='body']")
    page.locator("#body-list .body-item input").nth(0).uncheck()
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    # alpha is on screen but unchecked: Send asks which body to send
    page.wait_for_selector(".dialog")
    assert "alpha" in page.locator(".dialog-msg").text_content()
    page.click(".dialog-foot button:has-text('Send checked')")
    page.wait_for_function("document.querySelector('#r-status').textContent.trim().startsWith('200')", timeout=20000)
    page.click("#resp-tabs button[data-tab='tests']")
    heads = page.locator("#r-tests h4").all_text_contents()
    assert "Pre-request script" in heads and "Pre-request script · beta" in heads and not any(h.startswith("Test script") for h in heads)
    assert "alpha only" not in page.locator("#r-tests").text_content()
    page.click("#resp-tabs button[data-tab='body']")
    body = page.locator("#r-body").text_content()
    assert "x-all" in body and "x-own" in body and "beta" in body
    # the per-body scripts survive a reload
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Per-body scripts')")
    page.wait_for_selector("#editor-form:not([hidden])")
    page.click("#editor-tabs button[data-tab='tests']")
    assert options("test") == ["All bodies", "alpha ●", "beta"]
    page.select_option(test_scope, "p1")
    assert "alpha only" in shown("test")
    assert not page.errors, page.errors


def test_run_responses_show_in_editor(page, servers):
    """A run with the sample variables file: its responses appear as tabs in each request's response area."""
    app_url, echo_url = servers
    page.evaluate("""([app, echo]) => Promise.all([
        fetch(app + '/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          name: 'Run bodies', method: 'POST', url: echo + '/echo', body_type: 'json',
          bodies: [{id: 'r1', name: 'valid', body: '{"email": "{{email}}"}', enabled: true}, {id: 'r2', name: 'missing', body: '{}', enabled: true}]})}),
        fetch(app + '/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          name: 'Run plain', method: 'GET', url: echo + '/echo?who={{name}}'})})])""", [app_url, echo_url])
    page.reload()
    page.wait_for_selector("#btn-new")
    page.evaluate("() => { state.selected = state.requests.filter((r) => r.name.startsWith('Run ')).map(selEntry); openRunModal(); }")
    page.wait_for_selector("#run-data-try")
    # the sample file: a column per {{variable}} the requests use, three rows, iterations follow the rows
    page.click("#run-data-try")
    page.wait_for_function("document.querySelector('#run-data-info').textContent.includes('sample-variables.csv')")
    info = page.locator("#run-data-info").text_content()
    assert "3 row(s)" in info and "{{email}}" in info and "{{name}}" in info
    assert page.locator("#run-iterations").input_value() == "3" and page.locator("#run-iterations").is_disabled()
    page.click("#run-data-clear")
    assert not page.locator("#run-iterations").is_disabled()
    page.click("#run-data-try")
    page.wait_for_function("document.querySelector('#run-data-info').textContent.includes('sample-variables.csv')")
    page.fill("#run-name", "sample run")
    page.click("#modal-foot button.accent")
    page.wait_for_function("document.querySelector('#run-current .badge.completed') !== null", timeout=20000)
    # the request with two bodies: one tab per body and iteration, in body order
    page.click("#req-list .req-item:has-text('Run bodies')")
    page.wait_for_selector("#response:not([hidden]) #resp-bodies:not([hidden])")
    tabs = page.locator("#resp-bodies .rb-tab")
    assert [t.split("200")[0] for t in tabs.all_text_contents()] == ["valid #1", "valid #2", "valid #3", "missing #1", "missing #2", "missing #3"]
    run_info = page.locator("#resp-bodies .rb-run").text_content()
    assert "sample run" in run_info and "9/9 sent" in run_info
    tabs.nth(1).click()
    page.wait_for_function("document.querySelector('#resp-bodies .rb-tab.active').textContent.startsWith('valid #2')")
    assert "bob@example.com" in page.locator("#r-body").text_content()
    assert page.evaluate("document.querySelector('#body-list .body-item.active').textContent").startswith("valid")
    tabs.nth(5).click()
    page.wait_for_function("document.querySelector('#body-list .body-item.active').textContent.startsWith('missing')")
    # the GET without a body: one tab per iteration
    page.click("#req-list .req-item:has-text('Run plain')")
    page.wait_for_function("document.querySelector('#resp-bodies .rb-tab') && document.querySelector('#resp-bodies .rb-tab').textContent.startsWith('Iteration 1')")
    assert [t.split("200")[0] for t in page.locator("#resp-bodies .rb-tab").all_text_contents()] == ["Iteration 1", "Iteration 2", "Iteration 3"]
    page.click("#resp-bodies .rb-tab >> nth=2")
    page.wait_for_function("document.querySelector('#resp-bodies .rb-tab.active').textContent.startsWith('Iteration 3')")
    assert "Carol" in page.locator("#r-body").text_content()
    assert not page.errors, page.errors


def test_viewed_body_unchecked_asks_and_labels_response(page, servers):
    """Viewing a body that is not checked: Send asks, can send the viewed body once, and a response
    from another body is labelled as such instead of looking like the viewed body's."""
    app_url, echo_url = servers
    page.evaluate("""([app, echo]) => fetch(app + '/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        name: 'Viewed body', method: 'POST', url: echo + '/echo', body_type: 'json',
        bodies: [{id: 'v1', name: 'first', body: '{"n": 1}', enabled: false}, {id: 'v2', name: 'second', body: '{"n": 2}', enabled: true}]})})""", [app_url, echo_url])
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Viewed body')")
    page.wait_for_selector("#editor-form:not([hidden])")
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#body-list .body-item:has-text('first')")
    page.click("#btn-send")
    page.wait_for_selector(".dialog")
    page.click(".dialog-foot button.primary")   # Send “first”
    page.wait_for_function("document.querySelector('#r-status').textContent.trim().startsWith('200')", timeout=20000)
    assert 'n\\": 1' in page.locator("#r-body").text_content()
    # the checkboxes were not changed by that one-off send
    assert page.evaluate("state.bodies.map((b) => b.enabled)") == [False, True]
    # switching to "second" (never sent) flags the response as belonging to "first"
    page.click("#body-list .body-item:has-text('second')")
    page.wait_for_selector("#r-body-name.other:not([hidden])")
    assert "first" in page.locator("#r-body-name").text_content()


def test_headers_select_all(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Select all headers")
    page.click("#editor-tabs button[data-tab='headers']")
    page.evaluate("""() => renderKv(document.querySelector('#kv-headers'), [
        {key: 'A', value: '1', enabled: true}, {key: 'B', value: '2', enabled: false}])""")
    all_on = page.locator("#kv-headers .kv-all-on")
    assert page.evaluate("document.querySelector('#kv-headers .kv-all-on').indeterminate")
    all_on.click()
    assert page.locator("#kv-headers .kv-on:checked").count() == 2
    assert "Unselect all" in page.locator("#kv-headers .kv-all").text_content()
    all_on.click()
    assert page.locator("#kv-headers .kv-on:checked").count() == 0
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    saved = page.evaluate("fetch('/api/requests').then((r) => r.json()).then((l) => l.find((r) => r.name === 'Select all headers'))")
    assert [h["enabled"] for h in saved["headers"]] == [False, False]
    # toggling one row by hand updates the select-all box
    page.locator("#kv-headers .kv-on").nth(0).check()
    assert page.evaluate("document.querySelector('#kv-headers .kv-all-on').indeterminate")


def test_token_variable_dropdown(page, servers):
    app_url, echo_url = servers
    page.evaluate("fetch('/api/variables', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({variables: {access_token: 'abc', base: 'x'}})})")
    new_request(page, echo_url, "Token dropdown")
    page.click("#editor-tabs button[data-tab='auth']")
    page.select_option("#auth-type", "bearer")
    page.fill("#auth-token", "")
    page.click("#auth-token")
    page.wait_for_selector(".var-suggest button:has-text('{{access_token}}')")
    # auth-like names come first
    assert "access_token" in page.locator(".var-suggest button").nth(0).text_content()
    page.click(".var-suggest button:has-text('{{access_token}}')")
    assert page.input_value("#auth-token") == "{{access_token}}"
    assert page.locator(".var-suggest").count() == 0
    # typing {{ filters the list; Enter picks
    page.fill("#auth-token", "")
    page.type("#auth-token", "{{ba")
    page.wait_for_selector(".var-suggest button:has-text('{{base}}')")
    assert page.locator(".var-suggest button").count() == 1
    page.keyboard.press("Enter")
    assert page.input_value("#auth-token") == "{{base}}"
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    saved = page.evaluate("fetch('/api/requests').then((r) => r.json()).then((l) => l.find((r) => r.name === 'Token dropdown'))")
    assert saved["auth"]["token"] == "{{base}}"


def test_get_error_suggests_another_method(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "GET tip")
    page.fill("#f-url", echo_url + "/post-only")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#r-tip:not([hidden])", timeout=20000)
    assert "405" in page.locator("#r-tip").text_content()
    page.click("#r-tip .r-tip-m[data-m='POST']")
    assert page.input_value("#f-method") == "POST"
    assert page.locator("#r-tip").is_hidden()
    page.click("#btn-send")
    page.wait_for_function("document.querySelector('#r-status').textContent.trim().startsWith('200')", timeout=20000)
    assert page.locator("#r-tip").is_hidden()


def test_response_view_bar_stays_while_body_scrolls(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Long response")
    page.select_option("#f-method", "POST")
    page.click("#editor-tabs button[data-tab='body']")
    page.select_option("#f-body-type", "text")
    page.evaluate("""() => { if (!state.bodies.length) addBody(); }""")
    page.evaluate("""() => { const t = document.querySelector('#f-body'); const v = Array.from({length: 400}, (_, i) => 'line ' + i).join('\\n');
        if (t._enhanced) t._enhanced.setValue(v); else t.value = v; t.dispatchEvent(new Event('input', {bubbles: true})); }""")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#r-body .rv-bar", timeout=20000)
    page.wait_for_function("document.querySelector('#resp-panels').scrollHeight > document.querySelector('#resp-panels').clientHeight + 200")
    before = page.locator("#r-body .rv-bar").bounding_box()["y"]
    page.evaluate("document.querySelector('#resp-panels').scrollTop = 600")
    after = page.locator("#r-body .rv-bar").bounding_box()["y"]
    assert abs(after - before) < 1, (before, after)


def test_sending_shows_live_progress(page, servers):
    """A call in flight shows a live strip with the elapsed time (and a placeholder on the first send);
    it disappears with the response, and a later send keeps the previous response on screen."""
    app_url, echo_url = servers
    new_request(page, echo_url, "Live progress")
    page.fill("#f-url", echo_url + "/slow?seconds=2")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#resp-live .rl-skeleton", state="visible")
    assert page.locator(".url-row.sending").count() == 1
    assert "Sending GET" in page.locator("#resp-live .rl-text").text_content()
    page.wait_for_function("parseFloat(document.querySelector('#resp-live .rl-time').textContent) >= 0.5")
    page.wait_for_selector("#resp-live", state="detached", timeout=10000)
    assert page.locator(".url-row.sending").count() == 0
    assert page.locator("#r-status").text_content().startswith("200")
    # second send: the old response stays visible under the live strip, no placeholder
    page.click("#btn-send")
    page.wait_for_selector("#resp-live")
    assert page.locator("#resp-live .rl-skeleton").is_hidden()
    assert page.locator("#r-body .rv-bar").is_visible()
    page.wait_for_selector("#resp-live", state="detached", timeout=10000)


def test_keyboard_navigation(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Nav A")
    new_request(page, echo_url, "Nav B")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    # arrows move a focus ring over the visible requests (the module's data dir is shared,
    # so walk to a known row instead of assuming this test's requests come first)
    # the ring starts on the open request, which is the last row here, so move up first
    page.keyboard.press("ArrowUp")
    page.wait_for_selector("#req-list .req-item.kb-focus")
    above = page.locator("#req-list .req-item.kb-focus .name").text_content()
    page.keyboard.press("ArrowDown")
    focused = page.locator("#req-list .req-item.kb-focus .name").text_content()
    assert focused != above, "ArrowDown did not move the ring"
    page.keyboard.press("ArrowUp")
    assert page.locator("#req-list .req-item.kb-focus .name").text_content() == above, "ArrowUp did not come back"
    page.keyboard.press("ArrowDown")
    focused = page.locator("#req-list .req-item.kb-focus .name").text_content()
    # Enter opens whichever request has the ring
    page.keyboard.press("Enter")
    page.wait_for_function("name => document.querySelector('#f-name').value === name", arg=focused)
    # Space ticks it for a run, and again unticks
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press(" ")
    page.wait_for_function("document.querySelector('#selected-count').textContent.startsWith('1')")
    page.keyboard.press(" ")
    page.wait_for_function("document.querySelector('#selected-count').textContent.startsWith('0')")


def test_undo_delete_request(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Doomed")
    new_request(page, echo_url, "Keeper")
    # delete via the row menu: the row disappears at once, the toast offers Undo
    row = page.locator("#req-list .req-item", has_text="Doomed")
    row.hover()
    row.locator(".more").click()
    page.click("#ctx-menu button[data-act='del']")
    page.wait_for_selector("#toast .toast-act")
    assert page.locator("#req-list .req-item", has_text="Doomed").count() == 0
    # Undo brings it back with the same id (still on the server, never deleted)
    page.click("#toast .toast-act")
    page.wait_for_selector("#req-list .req-item:has-text('Doomed')")
    names = page.evaluate("fetch('/api/requests').then(r => r.json()).then(l => l.map(r => r.name))")
    assert "Doomed" in names
    # delete again and let the grace period pass: now it is gone on the server too
    row = page.locator("#req-list .req-item", has_text="Doomed")
    row.hover()
    row.locator(".more").click()
    page.click("#ctx-menu button[data-act='del']")
    page.wait_for_function(
        "fetch('/api/requests').then(r => r.json()).then(l => l.every(r => r.name !== 'Doomed'))",
        timeout=10000, polling=500,
    )


def test_undo_delete_body(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Body undo")
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#btn-body-add")
    page.fill("#body-name", "only body")
    set_editor(page, "#f-body", '{"a": 1}')
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-body-del")
    page.wait_for_selector("#toast .toast-act")
    assert page.locator("#body-list .body-item").count() == 0
    page.click("#toast .toast-act")
    page.wait_for_selector("#body-list .body-item:has-text('only body')")


def test_monitors_dialog(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Watched")
    # Monitors opens from the topbar as a dialog
    page.click("#btn-monitors")
    page.wait_for_selector("#mon-list .empty")
    # create a monitor over the one request; saving returns to the list
    page.click("#btn-mon-new")
    page.fill("#mon-name", "Health")
    page.fill("#mon-every", "5")
    page.check("#mon-pick input")
    page.click("#modal-foot button.primary")
    page.wait_for_selector(".mon-item")
    meta = page.locator(".mon-item .mon-meta").text_content()
    assert "every 5 min" in meta and "1 request" in meta
    # run now: a passing check appears (badge + sparkline bar)
    page.click(".mon-item [data-act='run']")
    page.wait_for_selector(".mon-item .badge.ok")
    page.wait_for_selector(".mon-spark i:not(.bad)")
    assert page.locator("#sb-monitors").is_hidden()
    # add a request that 404s: the next check fails, the status bar and topbar badge flag it
    page.keyboard.press("Escape")
    page.click("#btn-new")
    page.fill("#f-name", "Broken")
    page.fill("#f-url", echo_url + "/missing")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-monitors")
    page.click(".mon-item [data-act='edit']")
    page.locator("#mon-pick .mon-pick-row", has_text="Broken").locator("input").check()
    page.click("#modal-foot button.primary")
    page.wait_for_selector(".mon-item")
    page.click(".mon-item [data-act='run']")
    page.wait_for_selector(".mon-item .badge.failed")
    page.wait_for_selector("#sb-monitors:not([hidden])")
    page.wait_for_selector("#mon-badge:not([hidden])")
    # the results view lists what failed, and "Back to monitors" returns to the list
    page.click(".mon-item [data-act='results']")
    page.wait_for_selector("#modal .htable")
    assert "Broken" in page.locator("#modal .mon-fails").first.text_content()
    page.click("#modal-foot button.primary")
    page.wait_for_selector(".mon-item")
    # pause: the failing flag clears
    page.locator(".mon-item .switch span").click()
    page.wait_for_selector("#sb-monitors[hidden]", state="attached")
    page.wait_for_selector(".mon-item.off")
    # the ⚠ status chip opens the same dialog
    page.keyboard.press("Escape")
    page.wait_for_selector("#modal", state="hidden")


def test_streaming_send(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Stream me")
    page.fill("#f-url", echo_url + "/sse?n=6")
    # turn on "Stream the response live" in the request's Settings tab
    page.click("#editor-tabs button[data-tab='settings']")
    page.check("#f-stream")
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    # the live pane appears and fills while the stream runs
    page.wait_for_selector("#r-stream-live")
    page.wait_for_function("(document.querySelector('#r-stream-live') || {textContent:''}).textContent.includes('tick 1')")
    # when the stream ends, the normal response view takes over with the full body
    page.wait_for_selector("#resp-panels:visible", timeout=10000)
    page.wait_for_function("!document.querySelector('#r-stream-live')")
    assert "tick 5" in page.locator("#r-body").text_content()
    assert "200" in page.locator("#r-status").text_content()
    # the setting survives a reload
    page.reload()
    page.wait_for_selector("#req-list .req-item")
    page.click("#req-list .req-item:has-text('Stream me')")
    page.click("#editor-tabs button[data-tab='settings']")
    assert page.is_checked("#f-stream")


def test_settings_mcp_tab(page, servers):
    page.click("#btn-settings")
    page.click("#set-tabs button[data-tab='ai']")
    page.wait_for_function("document.querySelector('#mcp-cmd-code').textContent.includes('claude mcp add pyhttp')")
    cmd = page.locator("#mcp-cmd-code").text_content()
    assert "mcp" in cmd and "--data-dir" in cmd
    cfg = page.locator("#mcp-cmd-json").text_content()
    assert '"mcpServers"' in cfg and '"pyhttp"' in cfg


def test_mocks_dialog(page, servers):
    """Mock servers: create one, add a route, call it from the page, see it in the call log."""
    app_url, _echo = servers
    page.click("#btn-mocks")
    page.wait_for_selector("#mock-list .empty")
    page.click("#btn-mock-new")
    page.fill("#mk-name", "Payments API")
    page.click("#modal-foot button.primary")
    # creating a mock goes straight to its (empty) routes
    page.wait_for_selector("#btn-mock-route-new")
    assert "/mock/payments-api" in page.locator("#modal-body code.mono").first.text_content()

    page.click("#btn-mock-route-new")
    page.wait_for_selector("#mr-path")
    page.fill("#mr-path", "/users/:id")
    page.fill("#mr-name", "one user")
    page.fill("#mr-body", '{"id": "{{req.params.id}}"}')
    # the dialog previews the URL this route will answer on
    assert "/mock/payments-api/users/:id" in page.locator("#mr-preview").text_content()
    page.click("#modal-foot button.primary")
    page.wait_for_selector(".mock-route")

    # call it from the page itself: same origin, same port, no second server
    body = page.evaluate("fetch('/mock/payments-api/users/7').then(r => r.text())")
    assert json.loads(body) == {"id": "7"}
    page.evaluate("fetch('/mock/payments-api/missing')")

    page.click("#modal-foot button:has-text('Calls')")
    page.wait_for_selector(".mock-call")
    rows = page.locator(".mock-call")
    assert rows.count() == 2
    assert "no route matched" in rows.first.text_content()       # newest first: /missing
    assert "/users/7" in rows.nth(1).text_content()
    # clicking a call shows the headers and body it arrived with
    rows.nth(1).click()
    page.wait_for_selector(".dialog-backdrop")
    assert "user-agent" in page.locator(".dialog-msg").text_content()
    page.click(".dialog-foot button")

    # back on the list the hits are counted and the mock reads as live
    page.click("#modal-foot button:has-text('Back to mocks')")
    page.wait_for_selector(".mock-item .badge.ok")
    page.wait_for_selector(".mock-item .mon-meta:has-text('2 calls answered')")
    assert "/mock/payments-api" in page.locator(".mock-item .mock-url").text_content()
    page.wait_for_selector("#mock-badge:not([hidden])")

    # turning it off stops the answers and clears the topbar badge
    page.locator(".mock-item .switch span").click()
    page.wait_for_selector(".mock-item.off")
    page.wait_for_selector("#mock-badge[hidden]", state="attached")
    assert page.evaluate("fetch('/mock/payments-api/users/7').then(r => r.status)") == 503
    page.keyboard.press("Escape")
    page.wait_for_selector("#modal", state="hidden")
    # the 404 and 503 fetched on purpose above each log a console error; the rest must be clean
    page.errors[:] = [e for e in page.errors if "Failed to load resource" not in e]


def test_send_one_body_button_keeps_checkboxes(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "One body")
    page.select_option("#f-method", "POST")
    page.click("#editor-tabs button[data-tab='body']")
    for i in range(3):
        page.click("#btn-body-add")
        page.fill("#body-name", f"b{i}")
        set_editor(page, "#f-body", '{"which": %d}' % i)
        page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.locator("#body-list .body-item input").nth(0).uncheck()
    page.locator("#body-list .body-item input").nth(1).uncheck()
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    assert page.locator("#btn-send").text_content() == "Send"   # one checked body
    # the ➤ of an unchecked body sends just that body, without the "Send which body?" dialog
    page.locator("#body-list .body-item .bsend").nth(0).click()
    page.wait_for_selector("#response:not([hidden]) .jv")
    assert page.locator(".dialog-backdrop").count() == 0
    assert "200" in page.locator("#r-status").text_content()
    body_line = page.locator("#r-body .jv-node[data-path='body'] .jv-s").text_content()
    assert '\\"which\\": 0' in body_line
    state = "Array.from(document.querySelectorAll('#body-list .body-item')).map(li => [li.querySelector('input').checked, li.classList.contains('active')]).join()"
    assert page.evaluate(state) == "false,true,false,false,true,false"
    # the checkboxes were not touched, so the main Send still sends the one checked body (b2) — after the dialog, since b0 is viewed
    page.click("#btn-send")
    page.click(".dialog-backdrop button:has-text('Send checked')")
    page.wait_for_function("document.querySelectorAll('#resp-bodies .rb-tab').length === 2")
    assert page.evaluate(state) == "false,true,false,false,true,false"
    # with "No body" the per-body buttons are hidden
    page.select_option("#f-body-type", "none")
    assert page.locator("#body-list .body-item .bsend").nth(0).is_hidden()


def test_git_sync_from_topbar(page, servers, tmp_path):
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("git is not installed")
    app_url, echo_url = servers
    new_request(page, echo_url, "git ui one")
    # the top bar button opens Settings on its Git tab; off until a folder is chosen
    assert page.locator("#git-dot").is_hidden()
    page.click("#btn-git")
    page.wait_for_selector("#git-dir")
    assert page.locator("#git-apply").inner_text() == "Turn on Git sync"
    assert page.is_checked("#git-auto-commit") and page.is_checked("#git-auto-push") and not page.is_checked("#git-secrets")
    work = tmp_path / "defs"
    page.fill("#git-dir", str(work))
    page.click("#git-apply")
    page.wait_for_selector("#git-sync")                       # the panel redraws once it is on
    page.wait_for_selector("#git-dot.ok")
    assert "Last commit" in page.locator("#git-status").inner_text()
    assert "no remote" in page.locator("#git-status").inner_text()
    assert (work / "collection" / "git ui one.json").exists()
    page.click("#modal-close")
    # an edit in the app is committed without touching anything
    new_request(page, echo_url, "git ui two")
    page.wait_for_function("document.querySelector('#git-dot').classList.contains('busy')", timeout=8000)
    page.wait_for_selector("#git-dot.ok", timeout=15000)
    log = subprocess.run(["git", "-C", str(work), "log", "--format=%s"], capture_output=True, text=True, check=True).stdout
    assert "git ui two" in log
    assert (work / "collection" / "git ui two.json").exists()
    # turn it off again (the server is shared with the other tests)
    page.click("#btn-git")
    page.click("#git-off")
    page.wait_for_selector("#git-sync", state="detached")
    assert page.locator("#git-apply").inner_text() == "Turn on Git sync"
    assert page.locator("#git-dot").is_hidden()
    page.click("#modal-close")


def test_project_switch_from_topbar(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "In default")
    assert page.locator("#project-name").text_content() == "Default"
    page.click("#btn-project")
    page.click("#ctx-menu button[data-act='new']")
    page.fill("#dlg-in", "Client X")
    page.click(".dialog-backdrop button:has-text('Create and open')")
    page.wait_for_function("document.querySelector('#project-name').textContent === 'Client X'")
    page.wait_for_selector("#req-list .empty")
    assert page.locator("#req-list .req-item").count() == 0
    assert page.title() == "Client X · pyhttp"
    new_request(page, echo_url, "In client x")
    # the menu lists both, ticks the open one, and switching brings the other collection back
    page.click("#btn-project")
    labels = page.locator("#ctx-menu button").all_text_contents()
    assert any("Default" in t for t in labels) and any("✓" in t and "Client X" in t for t in labels)
    page.click("#ctx-menu button:has-text('Default')")
    page.wait_for_function("document.querySelector('#project-name').textContent === 'Default'")
    page.wait_for_selector("#req-list .req-item:has-text('In default')")
    assert page.locator("#req-list .req-item:has-text('In client x')").count() == 0
    # delete the other project from the menu
    page.click("#btn-project")
    page.click("#ctx-menu button:has-text('Delete “Client X”')")
    page.click(".dialog-backdrop button:has-text('Delete project')")
    page.wait_for_function("document.querySelector('.toast') && document.querySelector('.toast').textContent.includes('deleted')")
    page.click("#btn-project")
    assert page.locator("#ctx-menu button:has-text('Client X')").count() == 0
    page.keyboard.press("Escape")


def test_body_menu_duplicate_move_delete(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Body menu")
    page.click("#editor-tabs button[data-tab='body']")
    for i in range(2):
        page.click("#btn-body-add")
        page.fill("#body-name", f"b{i}")
        page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    names = "Array.from(document.querySelectorAll('#body-list .bname')).map(e => e.textContent).join()"
    active = "document.querySelector('#body-list .body-item.active .bname').textContent"
    assert page.evaluate(names) == "b0,b1" and page.evaluate(active) == "b1"
    # ⋯ on b0 → duplicate: the copy lands right after b0 and becomes active
    page.hover("#body-list .body-item >> nth=0")
    page.click("#body-list .body-item >> nth=0 >> .bmore")
    page.click("#ctx-menu button[data-act='dup']")
    assert page.evaluate(names) == "b0,b0 (copy),b1" and page.evaluate(active) == "b0 (copy)"
    # move b1 up, from a right-click menu, without touching the active body
    page.click("#body-list .body-item >> nth=2", button="right")
    page.click("#ctx-menu button[data-act='up']")
    assert page.evaluate(names) == "b0,b1,b0 (copy)" and page.evaluate(active) == "b0 (copy)"
    # delete a non-active body: the editor keeps showing the active one; undo brings it back
    page.hover("#body-list .body-item >> nth=0")
    page.click("#body-list .body-item >> nth=0 >> .bmore")
    page.click("#ctx-menu button[data-act='del']")
    assert page.evaluate(names) == "b1,b0 (copy)" and page.evaluate(active) == "b0 (copy)"
    page.click(".toast .toast-act")
    assert page.evaluate(names) == "b0,b1,b0 (copy)" and page.evaluate(active) == "b0 (copy)"
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    # the order survives a reload
    page.reload()
    page.wait_for_selector("#btn-new")
    page.click("#req-list .req-item:has-text('Body menu')")
    page.wait_for_function("document.querySelector('#f-name').value === 'Body menu'")
    page.click("#editor-tabs button[data-tab='body']")
    assert page.evaluate(names) == "b0,b1,b0 (copy)"


def test_find_shortcut_follows_focus(page, servers):
    app_url, echo_url = servers
    new_request(page, echo_url, "Find")
    page.select_option("#f-method", "POST")
    page.click("#editor-tabs button[data-tab='body']")
    page.click("#btn-body-add")
    set_editor(page, "#f-body", '{"a": 1}')
    page.wait_for_function("document.querySelector('#save-state').textContent === 'Saved'")
    page.click("#btn-send")
    page.wait_for_selector("#response:not([hidden]) .jv")
    where = "document.activeElement.closest('#response') ? 'response' : document.activeElement.closest('.cm-panel.cm-search') ? 'body-search' : 'other'"
    # focus in the body editor: Ctrl+F opens the editor's own search panel, not the response search
    page.click("#body-text-wrap .cm-content")
    page.keyboard.press("Control+f")
    page.wait_for_selector("#body-text-wrap .cm-panel.cm-search")
    assert page.evaluate(where) == "body-search"
    assert page.locator("#body-text-wrap .cm-panel.cm-search .cm-button").first.is_visible()
    page.click("#body-text-wrap .cm-panel.cm-search [name=close]")
    # focus elsewhere in the request pane (the body name) also searches the body
    page.click("#body-name")
    page.keyboard.press("Control+f")
    page.wait_for_selector("#body-text-wrap .cm-panel.cm-search")
    assert page.evaluate(where) == "body-search"
    page.click("#body-text-wrap .cm-panel.cm-search [name=close]")
    # focus in the response: Ctrl+F goes to the response search
    page.click("#r-body .jv")
    page.keyboard.press("Control+f")
    assert page.evaluate(where) == "response"
    assert page.evaluate("document.activeElement.classList.contains('rv-search')")
