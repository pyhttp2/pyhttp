# pyhttp — ideas, optimizations and open work
- different git repo url for each project

Status: `[x]` done · `[~]` in progress · `[ ]` open. Newest decisions at the top of each
section. Keep this file honest: when an item ships, tick it and say where (file or release).

## Shipped on 2026-11-13

- [x] **2.1.0**: a WebSocket address may start with a variable (`{{events_url}}/orders`): the
  request is recognised as a WebSocket one when the variable holds a `ws://` address, and follows
  the active environment. → `isWsUrl()` / `wsRefresh()` in `features.js`.

- [x] **WebSocket client.** A request with a `ws://` / `wss://` address connects instead of
  sending: Send becomes Connect, the response area becomes a console (live log, filter, Ping,
  a box to send from). Query parameters, headers, auth and `{{variables}}` of the request are
  used; `Sec-WebSocket-Protocol` offers subprotocols. No dependency: handshake and framing are
  in `pyhttp/wsclient.py`. → `/api/ws/*`, `renderWsPanel()` in `features.js`.
  - [ ] Not done: compression (permessage-deflate), proxies, history entries for sessions,
    WebSocket steps in runs and monitors, MCP tools.
- [x] **Saved example responses.** The pin button in the response bar keeps the response on
  screen with its request (Examples tab): listed in the documentation export, carried by
  exports and Git sync, and each one can become a mock route ("Routes from requests" also
  prefers a request's first example over its last history entry). Stored in the request's
  `options.examples`; the editor's autosave leaves them out and the server keeps them.
  → `/api/requests/{id}/examples*`, `mock.route_from_example()`.
- [x] **Compare two responses from the response strip** (⇄ Compare, when a request has two
  or more responses). Uses the history compare view on the responses in memory, so the whole
  body takes part. It is the same unified diff, not two columns. The send endpoints and run
  results now return `history_id`. → `compareResults()` / `showCompare()`.
- [x] **Request notes with Markdown preview**: already there (Details → Preview,
  `mdToHtml()`); the item was stale.
- [x] **Secrets outside the database.** Settings → General → "Keep 🔒 values in the system
  keychain" (off by default) moves the values of secret variables to Windows Credential
  Manager, the macOS Keychain (`security`) or the Secret Service (`secret-tool`); the database
  keeps a marker. No extra package. → `pyhttp/secretstore.py`, `db._seal/_unseal`.
  - [ ] Only the Windows backend has run against the real store. The macOS and Linux backends
    are written from the tools' documentation and are **untested**: try them on those systems.
- [x] **Docs export as HTML** (one self-contained page in the app's theme, rendered from the
  Markdown) and **Export documentation** in the folder ⋯ menu. Also fixes a crash in the
  Markdown export for GraphQL bodies with variables. → `docs.to_html()`.
- [x] **Response views: folder-level views** (folder ⋯ → Settings; every request inside shows
  them after its own) **and the whole body** for responses over 64 KB (kept in full in
  `outputs/_bodies/<history id>.body`, removed with the history entry). → `db.inherited()`,
  `runner.full_body()`.
- [x] **Git sync looks at the remote on a timer** (5 minutes by default; Settings → Git).
  Quiet when the remote cannot be reached. → `gitsync.Syncer.poll()`.
- [x] **Incremental tree render.** `renderList` patches the sidebar in place: rows are keyed
  by id and keep their DOM node while what they show is unchanged.
- [x] **Virtualised JSON tree.** A document with more than 4 000 values is drawn as a window
  of rows; expand/collapse, the path filter and search (over the document, not the DOM) work
  on all of it. The viewer can now load the whole body of a cut response.
  → `jsonTreeVirtual()` in `viewer.js`, `GET /api/history/{id}/body`.
- [x] **Accessibility**: tab strips follow the ARIA tabs pattern (`role=tab`, `aria-selected`,
  arrow keys, one tab stop); the modal is a `role=dialog` that takes focus, traps Tab and
  gives focus back. → `bindTabs()`, `openModal()`.
- [x] **Per-project database objects.** `db.Database` holds a connection and its lock;
  `db.open(path)` with `with db.using(other):` runs code against a second project while the
  app's own stays open. No "compare projects" UI yet: this is the groundwork for it.
- [x] `pyhttp doctor --vacuum`. → `tools.vacuum_db()`.
- [~] **WebKit CI job**: `ui-webkit` in `build.yml` runs the UI tests in WebKit on macOS for
  every push, non-blocking for now. The fold-marker click that failed in WebKit is now aimed
  at the gutter cell with the real mouse (`click_fold()` in `tests/test_ui.py`); that passes
  in Chromium but **has not run in WebKit** (no WebKit on the machine this was written on).
  Make the job blocking once it has been green.
- [x] Pushes to `main` only run the tests; installers are built for a release tag or a manual
  run. A late answer to an older sidebar search no longer overwrites the list.

## Shipped on 2026-11-12

- [x] **Response views.** A request can name HTML pages (files or addresses) that draw its
  response: the View tab of a response and a history entry show them, one at a time or all
  side by side, and open them in the browser. The page gets `?api=<address of the response>`
  and fetches it; "Create a starter view" writes a working page (chart, table, raw data).
  → `/api/views/*`, `/view/<id>/…`, `/response/<name>/<id>`, `mountViews()` in `features.js`,
  `static/view-starter.html`.

- [x] **Git sync.** Top bar → Git: the project's definitions (requests, folders, environments,
  variables, mocks, monitors) as one JSON file each in a Git repository, committed and pushed
  a few seconds after every change. Automatic commit and push are the default and can be
  switched off in Settings → Git; secret variable values stay out unless asked for.
  → `pyhttp/gitsync.py`, `/api/git*`, `renderGitPanel()` in `features.js`.
- [x] **Git sync, both ways.** Every sync fetches and merges the remote and folds what changed
  (also commits made by hand in the folder) back into the project, keeping ids; an empty
  project pointed at an existing repository loads it; the two cases that need a person (the
  folder holds another project, both sides changed the same request) are asked in the Git tab.
  → `gitsync.Syncer._catch_up/_adopt/_merge`, `db.apply_definitions()`, `/api/git/resolve`.

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

## Open

- [ ] WebSocket follow-ups: permessage-deflate, proxies, sessions in the history, WebSocket
  steps in runs and monitors, MCP tools.
- [ ] Try the keychain switch on macOS and Linux (see above); move OAuth client secrets and
  tokens typed straight into a request there too (today only 🔒 variables move).
- [ ] WebKit CI: confirm `ui-webkit` is green, then drop `continue-on-error`.
- [ ] A "compare projects" view, on top of `db.using()`.
- [ ] Side-by-side (two column) diff as an alternative to the unified one.
- [ ] Example responses: edit an example's body in place; pick which example "Routes from
  requests" uses.
