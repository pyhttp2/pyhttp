# pyhttp — ideas, optimizations and open work

Status: `[x]` done · `[~]` in progress · `[ ]` open. Newest decisions at the top of each
section. Keep this file honest: when an item ships, tick it and say where (file or release).

## Shipped in this pass (2026-11-06)

- [x] **Multiple projects** (user suggestion). A project is a separate workspace: its own
  requests, folders, environments, variables, history, runs, cookies, monitors and mocks.
  Each project is its own SQLite file and output folder under the data dir
  (`projects/<id>/`); the pre-existing data stays as the *Default* project, nothing is
  moved. Switch, create, rename and delete from the project button in the top bar; app
  settings (theme, proxy, fonts…) are copied into a new project so it does not start
  bare. The CLI runner takes `--project` and otherwise uses the active one.
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

- [ ] **Light sidebar endpoint.** `GET /api/requests` returns every request in full (all
  bodies, scripts, tests) to draw the tree. Add `?fields=list` (id, name, method, tags,
  folder_id, position, body count) and use it for the sidebar; load the full request on
  open. Matters past a few hundred requests or large bodies.
- [ ] **Incremental tree render.** `renderList` rebuilds the whole `<ul>`; patch rows in
  place (keyed by id) so a rename does not recreate hundreds of nodes.
- [ ] **Virtualised JSON tree** in the response viewer for multi-megabyte bodies (render
  only the visible rows; keep the path filter and search working on the full tree).
- [ ] **History/run list paging** instead of a hard `limit` (300 / 200) with no "more".
- [ ] **Startup**: `index.html` is re-read and regex-rewritten on each load; cache the
  rendered string per version (trivial, but it is on the hot path of every reload).

## Features

- [ ] **Project export/import as one file** (collection + environments + mocks + monitors)
  so a whole project can be moved between machines or shared in a repo.
- [ ] `pyhttp projects list|create|use <name>` on the CLI, and `--project` for `pyhttp mcp`.
- [ ] **Reorder bodies** (drag, or ▲▼ in a ⋯ menu per body) and per-body Duplicate / Delete in
  that menu, so a body can be managed without first making it active.
- [ ] **Saved example responses** per request (Postman "examples"): pin a response, show it
  in docs export, and let mocks be generated from it.
- [ ] **Collection documentation export** (Markdown/HTML): folders, requests, descriptions,
  example bodies, auth; the data is already there.
- [ ] **WebSocket client** (connect, send frames, log messages) — the most asked-for
  protocol after HTTP; SSE is already covered by streaming.
- [ ] **Diff two body responses side by side** right from the response strip (the history
  compare already has the diff engine; wire it to the `×N` tabs).
- [ ] **Request-level notes with Markdown preview** in Details.
- [ ] **Secrets outside the database**: an opt-in keyring backend for 🔒 variables.

## Quality and maintenance

- [ ] Accessibility pass: `aria-label` on every icon-only button, focus order in dialogs,
  `role=tab` on the tab strips; the ➤ body button already has a title, not a label.
- [ ] A `pyhttp doctor` command: data dir, DB integrity check, size of outputs, orphan run
  folders, with a "clean outputs older than N days" option.
- [ ] Replace the single-connection `db` module with per-project connections held by an
  object, so two projects could be open at once (needed before any "compare projects" UI).
- [ ] UI test for project switching in WebKit as well as Chromium (the body-list scroll bug
  only reproduced in WebKit — the engine the macOS app actually uses).
