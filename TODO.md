# pyhttp — ideas, optimizations and open work

Status: `[x]` done · `[~]` in progress · `[ ]` open. Newest decisions at the top of each
section. Keep this file honest: when an item ships, tick it and say where (file or release).

## Shipped on 2026-11-06 (second pass)

- [x] **Light sidebar endpoint.** `GET /api/requests?fields=list` returns only what the tree
  shows (id, name, method, url, tags, folder, body names/checks); the UI uses it for every
  list refresh. Full requests are loaded on open. → `app.py`, `loadRequests()`.
- [x] **History and run paging.** `/api/history` and `/api/runs` take `before=<ts>`; the
  panels show "Show older…" at the end of a page. History filters (text, status) now run in
  SQL over the whole table instead of over the newest 300 rows only.
- [x] **Whole-project export/import.** The pyhttp JSON export now carries mocks and
  monitors and keeps request ids, so monitors and "send this request first" links survive a
  round trip (ids are remapped on import). Replace mode wipes mocks/monitors too when the
  file has them.
- [x] **`pyhttp projects list|create|use|rename|delete`** and **`pyhttp doctor`**
  (integrity check per project database, row counts, output folder sizes, orphan run
  folders, `--clean-outputs DAYS`). `pyhttp mcp --project` serves one project. → `tools.py`.
- [x] **Body ⋯ menu** on every body row (and right-click): send only, duplicate, move up /
  down, delete, all without making the body active. Order is saved with the request.
- [x] **Documentation export (Markdown)** from Export ▾: folders, requests, params, headers
  (credential-looking values masked), auth, example bodies, tests, dependencies. → `docs.py`.
- [x] **Accessibility**: `aria-label` on every icon-only button (top bar, sidebar, body rows,
  history rows, run order, environments, key/value rows).
- [x] `index.html` is rewritten once per file change instead of on every reload.
- [x] **UI tests can run in WebKit**: `PYHTTP_UI_BROWSER=webkit pytest tests/test_ui.py`.
  Doing so found a real macOS-app bug: **Send did nothing when a body field still had
  focus** (WebKit drops a click whose button text is replaced between mousedown and mouseup;
  the Send label was re-rendered on every blur). Fixed in `renderSendButton`.

## Shipped on 2026-11-06 (first pass)

- [x] **Multiple projects** (user suggestion). A project is a separate workspace: its own
  requests, folders, environments, variables, history, runs, cookies, monitors and mocks.
  Each project is its own SQLite file and output folder under the data dir
  (`projects/<id>/`); the pre-existing data stays as the *Default* project, nothing is
  moved. Switch, create, rename and delete from the project button in the top bar; app
  settings (theme, proxy, fonts…) are copied into a new project so it does not start bare.
  The CLI runner takes `--project` and otherwise uses the active one.
  → `pyhttp/projects.py`, `/api/projects*`, top bar menu, `tests/test_backend.py`.
- [x] **Per-body ➤ send** and the body list scroll jump (WebKit resets a rebuilt list to the
  top; the list and the sidebar now keep their scroll position).
- [x] **Autosave no longer reloads the sidebar on every keystroke.** The sidebar only shows
  name, method, tags, folder and the body count, so the three list calls after a save run
  only when one of those changed. Typing in a body or header no longer re-renders the tree.
- [x] **Ctrl/Cmd+Shift+Enter sends only the body you are viewing**, the keyboard twin of the
  ➤ button.
- [x] FastAPI `on_event` → `lifespan` (removes the deprecation warning printed by every test run).

## Performance

- [ ] **Incremental tree render.** `renderList` rebuilds the whole `<ul>`; patch rows in
  place (keyed by id) so a rename does not recreate hundreds of nodes. Less urgent now that
  the list payload is light and autosave rarely triggers it.
- [ ] **Virtualised JSON tree** in the response viewer for multi-megabyte bodies (render
  only the visible rows; keep the path filter and search working on the full tree).

## Features

- [ ] **Saved example responses** per request (Postman "examples"): pin a response, show it
  in the docs export, and let mocks be generated from it.
- [ ] **WebSocket client** (connect, send frames, log messages) — the most asked-for
  protocol after HTTP; SSE is already covered by streaming.
- [ ] **Diff two body responses side by side** right from the response strip. Needs the
  send endpoints to return the history id of the entry they wrote (they do not today), then
  the history compare view can be reused as is.
- [ ] **Request-level notes with Markdown preview** in Details (a small renderer, no
  dependency; the docs export already emits Markdown).
- [ ] **Secrets outside the database**: an opt-in keyring backend for 🔒 variables.
- [ ] Docs export as HTML (same content as the Markdown, with the project's theme) and a
  per-folder entry in the folder ⋯ menu.

## Quality and maintenance

- [ ] `role=tab` / `aria-selected` on the tab strips and a focus trap in dialogs (labels on
  icon buttons are done).
- [ ] Replace the single-connection `db` module with per-project connections held by an
  object, so two projects could be open at once (needed before any "compare projects" UI).
- [ ] **WebKit CI job**: run `PYHTTP_UI_BROWSER=webkit` in CI on macOS. One pre-existing
  failure to fix first: `test_editor_comment_toggle_and_json_status` cannot click the
  CodeMirror fold-gutter marker in WebKit (the fold itself works by hand; the marker is
  probably not hit-testable at the position Playwright computes).
- [ ] `pyhttp doctor --vacuum` to compact databases after large history deletions.
