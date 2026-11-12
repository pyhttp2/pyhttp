# pyhttp

A local API client and alternative to Postman — it imports Postman collections
and runs Postman scripts — with a built-in collection runner, in its
own desktop window. Everything stays on your machine: requests live in a SQLite
file, every run writes its responses to plain files, and the backend listens
only on `127.0.0.1`.

> **macOS: "Update with Homebrew" fails on an older pyhttp?** Homebrew 7 must
> trust the pyhttp tap once; pyhttp 2.0.7 and older only report
> `brew upgrade --cask failed (exit code 1)`. Run this in a terminal, then update
> again (from the app, or with the second line):
>
> ```sh
> brew trust pyhttp2/pyhttp
> brew update && brew upgrade --cask pyhttp2/pyhttp/pyhttp
> xattr -dr com.apple.quarantine /Applications/pyhttp.app
> ```
>
> The last line stops the "Apple could not verify pyhttp" popup that follows a
> Terminal upgrade (the build is not notarized; the in-app updater clears the flag
> itself). Your data in `~/Library/Application Support/pyhttp` is kept. Installed from the
> `.dmg` instead? Download the new one and drag pyhttp over the old app.
> Details under [Updates](#updates).

## Features

- Save requests with **name, description, tags**, method, URL, query params,
  headers, body (JSON / text / XML / form), timeout and SSL verification.
- **Autosave**: every edit, new body or pasted JSON is saved half a second after
  you stop typing. The header shows *Saving… / Saved*; Ctrl/Cmd+S saves at once.
- **Bulk edit** for headers, params and variables: one `key: value` per line,
  `//` at the start of a line disables it, and a pasted `?a=1&b=2` query string
  becomes separate params.
- Each request in the sidebar has a **⋯ menu** (or right-click) with Duplicate
  and Delete.
- Compact editor: method, URL and Send sit at the top. Tags and description
  live behind **Details** and show as small chips when collapsed.
- **Named bodies**: a request can hold many bodies, each with its own name
  (for example `valid user`, `missing email`). Search bodies by name or content,
  and tick the ones to send. **Send** hits the request once per checked body and
  saves every output to a run folder, with the body name in the file names. The
  ➤ next to a body sends just that body, without touching the checkboxes
  (Ctrl/Cmd+Shift+Enter does the same for the body in the editor); its ⋯ menu
  duplicates, reorders or deletes a body without opening it.
- **Search** across name, description, URL, tags and body names; filter by tag chips.
- **Monitors** (topbar → Monitors): picked requests are sent again every N
  minutes while the app is open. A check fails when a request errors, answers
  4xx/5xx or fails a test; failing monitors show in the status bar and can POST
  to a webhook. "Run now" sends a check at once.
- **Mock servers** (topbar → Mocks): fake endpoints answered by pyhttp itself,
  **on the port it already runs on** — `http://127.0.0.1:8765/mock/<name>/…`, with
  no second process to start. Give a route a method, a path (`/users/:id`), a
  status and a body, and point your front end, a test or another request at it.
  See *Mock servers* below.
- **Streamed responses**: tick *Stream the response live* in a request's
  Settings tab for Server-Sent Events, chunked and AI-style streaming endpoints —
  the body shows as it arrives, and Cancel stops the stream but keeps what came.
- **Keyboard first**: ↑/↓ move through the sidebar, Enter opens, Space ticks for
  a run, Delete removes. Deleting a request or body is **undoable** for a few
  seconds from the toast.
- **Folders**: organise requests in nested folders (for example `QA`, `Prod`,
  `QA / Users`). Drag requests or folders onto a folder, or use the ⋯ menu:
  Add request, Add folder, Run folder, Rename, Move, Delete. Ticking a folder
  selects every request inside it for a run.
- **Environments**: create Dev, QA, Staging, Prod (one click each) or your own.
  Each has its own variables, which override the global ones while it is active.
  Switch from the selector in the top bar; Prod is highlighted in red. Runs
  record which environment they used. Mark a variable **secret** (🔒) and its
  value is masked on screen and replaced by `••••••` in the saved history and run
  files (Postman's `secret` type is kept on import and export).
- In-app dialogs for every confirmation (no browser pop-ups); Escape cancels,
  Enter confirms.
- **Select many** requests, reorder them, and **run them one by one**.
  Progress shows live; each response is stored in the run's output folder.
- **Variables**: `{{base_url}}` anywhere in URL, params, headers or body.
  Global variables apply everywhere; the active environment overrides them.
- **Chaining**: inside a run, `{{prev.json.token}}`, `{{prev.status}}`,
  `{{prev.body}}` and `{{prev.headers.content-type}}` read the previous response.
- Run history, cancel, stop-on-failure, delay between requests.
- Import / export the whole collection as JSON, including folders and
  environments. Import asks whether to add to or replace your collection.
- **Body editor** (CodeMirror): line numbers, code folding, bracket matching,
  syntax colours, `Ctrl+/` comments a line out (removed before the request is
  sent, like Postman), Tab indents, `Ctrl+B` beautifies and keeps your comments,
  and a live status line says "Valid JSON" or points at the broken line.
- **Response viewer**: a collapsible, colour-coded JSON tree (click a key to copy
  its path), Raw and Preview (HTML, images, PDF) views, wrap toggle, a path filter
  (`data.items[*].id`) and `Ctrl+F` search with match count. Save the body to a file.
- **Auth tab**: Basic, Bearer, API key (header or query), Digest, **OAuth 2.0**
  (client credentials or password grant: the token is fetched on the first send,
  cached until it expires, refreshed with the refresh token, and fetched again
  after a `401`; *Get new token* shows its status), or inherit from the folder. Folders have their own settings (auth, headers, variables) that
  every request inside inherits.
- **Body types**: JSON, text, XML, HTML, JavaScript, form (key/value rows),
  form-data with file fields, binary file, GraphQL (query + variables).
- **Scripts tab** (Pre-request and Post-response, like Postman): **tests without
  code** (status, header, body, JSON path and response-time assertions; results in
  the response's Tests tab and in run reports), each for all bodies or for one named
  body; **extract** values from a response into environment or global variables
  (`{{token}}` in the next request); **pre-request variables**; and **scripts** in
  JavaScript with Postman's `pm` API (`pm.test`, `pm.expect`, `pm.response`,
  `pm.environment.set`…) so Postman scripts run unchanged, or in Python with a `py`
  object. `{{body_name}}` (also `body_name` in scripts) tells which body is being sent.
  With several bodies, each script editor has a **for** picker: write a script for
  *All bodies* or for one named body; a body's own pre-request / test script runs
  after the all-bodies one, only when that body is sent.
- **Dependent requests**: in a request's Settings tab, pick another request to
  send first (typically one that fetches a token and saves it with an
  extraction or `pm.environment.set`), and how long its result stays valid.
  The token request runs before the first send, is reused until it expires,
  is fetched once even for parallel sends, and is refreshed automatically with
  one retry when the dependent request comes back 401 or 403.
- **Dynamic variables**: `{{$guid}}`, `{{$timestamp}}`, `{{$isoTimestamp}}`,
  `{{$randomInt 1 100}}`, `{{$randomEmail}}`, `{{$date}}` and more. Path
  variables (`/users/:id`) get their value from a Params row. Unresolved
  variables are flagged before sending.
- **Code snippets** (`</>`): cURL, Python (requests, httpx), JavaScript fetch,
  Node axios, PowerShell, raw HTTP, Go, with variables and auth resolved.
- **Import** Postman Collection v2.1 files and Postman environments, **OpenAPI 3 /
  Swagger 2** specs (JSON or YAML: one folder per tag, `{{baseUrl}}` from the
  servers, bodies from examples and schemas, auth from the security schemes),
  browser **HAR** files, or paste a `curl` command. **Export** as a Postman
  collection (whole collection or one folder).
- **Tabs** for open requests (Ctrl+W closes) and drag to **reorder** requests
  and folders.
- **History** in the right-hand panel: **Run history** lists collection runs,
  **Request history** every single request sent. Filter it by text or by status
  (2xx, 3xx, 4xx, 5xx, errors), send an entry again with one click (↻), reopen
  its response, open a copy as a new request, or **compare** two entries (status,
  time, size, and a line diff of body, headers and request; JSON keys are sorted
  so only real changes show).
- **Split panes**: the request editor and the response are two panes that scroll
  on their own, so the URL, the tabs and the response status always stay in view.
  Drag the edge between them (and the edges of the sidebar and the right-hand panel)
  to resize; double-click an edge to reset. The response goes **below** the request
  or to its **right**: switch in the status bar ("Response: below | right"), with the
  button in the response header, or in Settings. The sidebar hides behind the
  **☰** button (Ctrl/Cmd+\). Errors wrap instead of scrolling sideways.
- **Cookie jar** like a browser: cookies are stored per domain, sent on later
  requests, listed in a Cookies response tab and editable in a manager.
- **Runner options**: iterations, a CSV/JSON **variables file** (one row of
  variable values per iteration, columns as `{{variables}}`; the run dialog
  offers a sample file with a column for each variable your requests use, to
  download or to run with), failing
  tests fail the run, "failed only" filter, HTML/JSON run report. While a run
  goes, every request it sends shows its responses beside the request: one tab
  per body and iteration, so you can compare them without opening the report.
- **Settings**: follow redirects, max redirects, proxy with bypass list, client
  certificates per host, default timeout, User-Agent (default `pyhttp/<version>`), editor font size, response
  below or beside the request, and in-app updates (the Windows builds and Homebrew installs update themselves, see *Updates* below).
- **Git sync** (topbar → Git): the project's requests, environments, mocks and monitors as
  JSON files in a Git repository, committed and pushed automatically on every change. See
  *Git sync* below.
- Native desktop window using the OS webview (WebView2 / WKWebView / WebKitGTK),
  with native save/open dialogs for export and import.
- No Node, no Electron, no build step to run it. Python 3.10+ only (the editor is a
  prebuilt CodeMirror bundle checked into the repo).

## Projects

A project is a separate workspace: its own requests, folders, environments, variables,
history, cookies, run outputs, mocks and monitors. Click the project name in the top bar to
switch, create, rename or delete one. Everything you had before projects existed is the
*Default* project and stays where it was; each new project lives in its own folder under
the data directory (`projects/<id>/pyhttp.db` and `projects/<id>/outputs`). App settings
(theme, proxy, fonts…) are copied into a new project so it does not start bare. The CLI
runner uses the active project, or `pyhttp run --project "Shop API" …` for another one;
`pyhttp mcp --project …` serves one project to an MCP client.

From a terminal: `pyhttp projects` lists them, `pyhttp projects create NAME [--use]`,
`use NAME`, `rename OLD NEW` and `delete NAME` manage them. `pyhttp doctor` checks every
project's database (integrity, row counts), reports run output sizes and orphan folders,
and `pyhttp doctor --clean-outputs 30` deletes run folders older than 30 days.

**Export ▾ → pyhttp JSON** writes the whole project (collection, environments, variables,
mocks, monitors) to one file that imports into another project or machine; monitors and
"send this request first" links are kept. **Export ▾ → Documentation** writes the
collection as Markdown: folders, requests, parameters, headers (credential-looking values
masked), auth, example bodies and tests.

## Git sync

Click **Git** in the top bar, choose a folder and press *Turn on Git sync*. From then on the
project's definitions live in a Git repository as well: every request, folder, environment,
mock and monitor is one JSON file, and a change is **committed and pushed by itself** a few
seconds after you stop editing, with the names of what changed as the commit message.

```
pyhttp.json                      marks the folder as written by pyhttp
variables.json                   global variables
monitors.json
collection/Users/_folder.json    a folder's own settings
collection/Users/Login.json      one request
environments/Prod.json
mocks/shop.json
```

- **The folder** can be a repository, a folder inside one (keep the requests next to the code
  they test), or neither — then a repository is created in it. Only the paths above are
  staged, committed or deleted; other files and uncommitted work in the same repository are
  left alone.
- **The remote** is optional. Give a URL and it becomes `origin`; leave it empty and the
  remote the repository already has is used. Without any remote, commits stay local. Pushing
  uses your own Git credentials (credential manager or SSH key). A push that fails is tried
  again with the next change, and the dot on the Git button turns red with the reason in its
  tooltip.
- **Settings → Git** has the switches: *Commit automatically*, *Push automatically* and *Pull
  automatically* (all on by default; with them off, *Sync now* does it), and *Include secret values*
  (off by default: values of variables marked 🔒 are written empty). Tokens and passwords
  typed straight into a request are part of that request and are always written — use a 🔒
  variable for anything that must not reach the repository.
- History, run outputs and cookies are never written. Each project has its own Git folder.
  Sending a request does not commit by itself; variables a script sets go out with the next edit.
- **It works both ways.** With every sync (and when the app starts) the remote is fetched and
  merged, and what changed there is read into the project: a teammate's edited request
  replaces yours under the same id, new ones appear, deleted ones go, and the window reloads
  to show it. Commits you make by hand in the folder are read back the same way. Edits to
  the files that are *not committed* are overwritten by the next sync.
- **Joining a repository:** give a new, empty project the remote URL and an empty folder; it
  starts from what the remote has. If the project already has content and the folder holds a
  different pyhttp project, nothing is replaced until you choose *Load the folder into this
  project* or *Overwrite the folder with this project*.
- **Conflicts:** when you and the remote changed the same request, the merge is undone and
  the Git tab asks: *Take the remote version* or *Keep my version*. Changes to different
  requests merge by themselves. If a pull arrives while you are typing in a request, the
  window does not reload under you (a toast offers *Reload*), and what you save afterwards
  wins for that request.
- In a repository shared with code, *Pull automatically* merges the remote branch into the
  branch that is checked out, code included. Turn it off there if you would rather pull yourself.

## Mock servers

An API you need does not exist yet, or you want it to fail on demand. Open
**Mocks** in the top bar, add a mock server, and give it routes. They are
answered by pyhttp itself on the port it already listens on, so there is no
second server to start and no second port to remember:

```
http://127.0.0.1:8765  +  /mock  +  /payments-api  +  /users/42
└ wherever pyhttp runs ┘            └ the mock ┘      └ the route ┘
```

- **Routes** are a method, a path, a status, headers and a body. A path may hold
  parameters written either way (`/users/:id` or `/users/{id}`); `*` matches one
  segment and `**` the rest of the path. `ANY` answers every method. Exact paths
  win over parameter ones, so `/users/me` and `/users/:id` can live side by side.
- **The body is a template**, so a route can answer differently every time and can
  read the call it is answering:
  `{{req.params.id}}` (a path parameter), `{{req.query.limit}}`,
  `{{req.headers.x-key}}`, `{{req.body}}`, `{{req.json.user.name}}`,
  `{{req.method}}`, `{{req.path}}` — plus your own `{{variables}}` and the
  dynamic ones (`{{$guid}}`, `{{$randomEmail}}`…).
- **One path, several answers**: give a route a query condition (`fail=1`) and it
  only answers calls that carry it, so the happy path and the 500 can share
  `/payments/:id`. A route with a condition wins over one without.
- **Delays** per server and per route, to see how your app behaves on a slow API.
- **CORS** headers and preflight (`OPTIONS`) answers are sent by default, so a
  browser app on another origin can call the mock.
- **Calls**: every request a mock answered, newest first, with the headers and
  body it arrived with and which route matched — the quickest way to see what
  your app is really sending. Unmatched calls are flagged, and a 404 from a mock
  lists the routes it does know.
- **From requests…** builds one route per request you already have, answering
  with the response that request last gave.
- **Try** opens a route as a normal request, with the URL written as
  `{{mock_url}}/<name>/…`. `{{mock_url}}` is this app's own `/mock` base, so such
  requests keep working whichever port the app got.
- `GET /mock` lists what is being mocked on this port, for a quick look from
  `curl` or a browser.

Mocks live in the same SQLite file as everything else, and are not part of the
collection export.

## Run it

Requires Python 3.10 or newer.

**Any OS, with pip** (the PyPI name is `pyhttp-app`; the command stays `pyhttp`):

```sh
pip install pyhttp-app     # or: pipx install pyhttp-app
pyhttp
```

On Linux, install the GTK webview bindings first (see *Linux* below) or use
`pyhttp --browser`. `brew install pyhttp2/pyhttp/pyhttp` installs the
same package through Homebrew (macOS and Linux, Intel and ARM; the tap is
[pyhttp2/homebrew-pyhttp](https://github.com/pyhttp2/homebrew-pyhttp)) — or tap
once with `brew tap pyhttp2/pyhttp`, after which plain `brew install pyhttp`,
`brew upgrade pyhttp` and `brew install --cask pyhttp` all work.

**Windows**: double-click `run.bat`. The window opens without a console.
WebView2 ships with Windows 10/11.

**macOS**:

```sh
chmod +x run.sh
./run.sh
```

**Linux**: Ubuntu/Debian users can install the `.deb` instead (see *Desktop apps*).
From source, install the GTK webview bindings once, then use `run.sh`.

```sh
sudo apt install python3-gi gir1.2-webkit2-4.1        # Debian / Ubuntu
sudo dnf install python3-gobject webkit2gtk4.1        # Fedora
chmod +x run.sh
./run.sh
```

The scripts create a `.venv` on first start and reinstall dependencies
whenever `requirements.txt` changes.

### Modes

```sh
python -m pyhttp                 # native window (default)
python -m pyhttp --browser       # open in your web browser at http://127.0.0.1:8765/
python -m pyhttp --server        # API only, for scripting
python -m pyhttp --debug         # native window with devtools
python -m pyhttp --data-dir PATH # keep data somewhere else
python -m pyhttp mcp             # MCP server on stdio, so AI tools can drive pyhttp
```

If no native webview is available, pyhttp prints why and falls back to the
browser automatically.

### Headless runs for CI

`pyhttp run` runs requests without the UI, prints each result, writes reports and
exits with `0` when everything passed, `1` when something failed and `2` for usage
errors, like Postman's newman.

```sh
python -m pyhttp run                                  # every request in your collection
python -m pyhttp run "API/Users" Login --env QA       # folders (name or path) and requests (name or id)
python -m pyhttp run collection.json --env-file qa.postman_environment.json
python -m pyhttp run openapi.yaml --var baseUrl=http://localhost:8000 --list
python -m pyhttp run --tag smoke -n 3 --data users.csv --bail \
    --junit out/junit.xml --html out/report.html --json out/results.json
```

A collection file (Postman v2.1, pyhttp export, OpenAPI, HAR) is run from a
throwaway data folder, so your own collection is not touched. Other options:
`--delay MS`, `--insecure`, `--timeout S`, `--no-fail-on-http-error`,
`--no-fail-on-tests`, `-q`, `--no-color`. Runs started from the command line also
appear in the app's Run history. See `python -m pyhttp run --help`.

### MCP server (AI tools drive pyhttp)

`python -m pyhttp mcp` serves the collection over the
[Model Context Protocol](https://modelcontextprotocol.io) on stdio: an AI tool
can list, create and edit requests, send them, run folders or tags like the
collection runner, read history, switch environments and set variables. Every
tool goes through the same code path as the UI, and the data folder is shared
with the app, so both can be open at the same time.

```sh
claude mcp add pyhttp -- python -m pyhttp mcp          # Claude Code
```

```json
{ "mcpServers": { "pyhttp": { "command": "python", "args": ["-m", "pyhttp", "mcp"] } } }
```

## Where data goes

```
data/
  pyhttp.db                      # requests, folders, environments, variables, run history (SQLite)
  outputs/
    2026-10-07_18-30-00_smoke/   # one folder per run
      summary.json               # run status + one line per request
      01_login.json              # full detail: resolved request, headers, body, timing
      01_login.body.json         # raw response body (extension follows content-type)
      02_create-user__valid-user.json        # one pair per named body
      02_create-user__valid-user.body.json
      02_list-users.json
      02_list-users.body.json
```

Use `--data-dir` to put this somewhere else.

Sending with a single checked body shows the response immediately and does not
write files. Sending with two or more checked bodies, or using **Run selected**,
always saves outputs.

## Desktop apps (installer, .exe, .dmg and .deb)

Packaged builds bundle Python and every dependency, so users just double-click.

| OS | File | Data folder |
|---|---|---|
| Windows 10/11 | `pyhttp-<version>-windows-x64-setup.exe`: installer (per user, no admin rights, into `%LOCALAPPDATA%\Programs\pyhttp`, Start menu entry, optional desktop icon, uninstaller in *Apps*) | `%APPDATA%\pyhttp` |
| Windows 10/11 | `pyhttp-<version>-windows-x64.exe`: portable, one file, no install | `%APPDATA%\pyhttp` |
| macOS 11+ | `brew install --cask pyhttp2/pyhttp/pyhttp`, or `pyhttp-<version>-macos-arm64.dmg` (drag pyhttp to Applications). Update from an older pyhttp fails? `brew trust pyhttp2/pyhttp` once, see [Updates](#updates) | `~/Library/Application Support/pyhttp` |
| Ubuntu 22.04+, Debian 12+ | `sudo apt install ./pyhttp-<version>-linux-amd64.deb`: installs to `/usr/lib/pyhttp`, adds `pyhttp` to the PATH and an app-menu entry; uses the distro's GTK and WebKitGTK (pulled in as dependencies) | `~/.local/share/pyhttp` |

Running from source keeps using `./data`. In the packaged app, output that would
go to a console is written to `pyhttp.log` in the data folder. Every release also
ships `SHA256SUMS.txt` with the checksums of the files above.

### Updates

pyhttp looks up the latest release at start (Settings → About: once an hour, or
*Check now*) and shows an **⬆ Update** button in the top bar when a newer version
exists. What the button does depends on how pyhttp was installed:

| Installed as | Update button |
|---|---|
| Windows installer | **Install and restart**: downloads the new installer, verifies it against `SHA256SUMS.txt`, runs it quietly (`/VERYSILENT`) after pyhttp has closed, and the installer starts pyhttp again. |
| Windows portable `.exe` | **Install and restart**: downloads and verifies the new `.exe`, then a small helper (`updates/apply-update.cmd` in the data folder) waits for pyhttp to exit, swaps the file in place (same name, same folder) and starts it again. |
| macOS, installed with Homebrew | **Update with Homebrew**: points a tap made before the move at the current repository, runs `brew trust pyhttp2/pyhttp` (Homebrew 7 loads nothing from a third-party tap until it was trusted once), `brew update` and `brew upgrade --cask pyhttp2/pyhttp/pyhttp` with the output shown in the dialog, removes the quarantine flag and restarts pyhttp. When brew fails, the dialog shows brew's own error line and the command that fixes it. |
| macOS `.dmg` | Download of the new `.dmg`, plus the Homebrew commands to switch (after that, updates install themselves). |
| Linux `.deb` | Download of the new `.deb` plus the `sudo apt install ./pyhttp-<version>-linux-amd64.deb` command to run. |
| From source | Download links and `git pull`. |

**"Update with Homebrew" failed (exit code 1) on pyhttp 2.0.7 or older.** Homebrew 7
loads nothing from a third-party tap until it was trusted once. Macs that
installed pyhttp before the move to `github.com/pyhttp2/pyhttp` trusted the tap
under its old address; once pyhttp points the tap at its new home that trust no
longer applies, and `brew upgrade` stops with *Refusing to load cask
pyhttp2/pyhttp/pyhttp from untrusted tap*. Trust the tap once, then update again
(from the app, or with the second line):

```sh
brew trust pyhttp2/pyhttp
brew update && brew upgrade --cask pyhttp2/pyhttp/pyhttp
xattr -dr com.apple.quarantine /Applications/pyhttp.app
```

The third line clears the quarantine flag that a Terminal upgrade leaves on the
new app; without it the next launch shows "Apple could not verify pyhttp is free
of malware" (see *First launch* below). From 2.0.8 on pyhttp runs the trust step itself and shows Homebrew's own error
line with the fix when something else goes wrong.

Requests, settings and history are kept: they live in the data folder, which no
update touches. The backend is `pyhttp/updater.py` (`POST /api/update/install`,
`GET /api/update/status`); the UI polls the status and shows a progress bar.

### Build on your machine

PyInstaller does not cross-compile: build the `.exe` on Windows and the `.dmg` on a Mac.

```sh
pip install -r requirements.txt -r requirements-build.txt
python packaging/build.py          # result goes to dist/
```

On Windows you can use the project venv: `.venv\Scripts\python packaging\build.py`.
The Windows build produces the portable `.exe` and, when [Inno Setup](https://jrsoftware.org/isdl.php)
6.3+ or 7 is installed, the installer from `packaging/pyhttp.iss` (ISCC.exe is found on
PATH, in Program Files or through the `ISCC` environment variable; without it the
installer is skipped with a note).
A `.dmg` built on an Apple Silicon Mac runs on Apple Silicon; build on an Intel Mac for Intel.

On Ubuntu/Debian the same command builds the `.deb`. Install the GTK bindings and
packaging tools first (`sudo apt install libgirepository1.0-dev libcairo2-dev pkg-config
gir1.2-gtk-3.0 gir1.2-webkit2-4.1 dpkg-dev`, then `pip install "PyGObject<3.51" pycairo`
in the venv). The package leaves GTK, WebKitGTK and the other system libraries to the
distro and declares them as dependencies (`packaging/build.py` works them out from the
bundle, like `dpkg-shlibdeps`). Build on the oldest release you want to support: a
`.deb` built on Ubuntu 22.04 runs on 22.04, 24.04 and Debian 12+.

### Build both on GitHub (no Mac needed)

`.github/workflows/build.yml` builds the Windows `.exe` and the macOS `.dmg` on
GitHub's machines. Every push to `main` only runs the tests (Windows, macOS, Linux).
For a test build without releasing, run the workflow by hand from the Actions tab
(files appear under the run's *Artifacts* for a day). To release:

```sh
python packaging/bump.py 0.1.2
```

That sets `__version__`, commits, creates the tag `v0.1.2` and pushes both. The
tag run publishes the installer, the portable `.exe`, the `.dmg`, `SHA256SUMS.txt`
and the license files to this repository's Releases page, uploads `pyhttp-app` to
PyPI (secret `PYPI_API_TOKEN`) and updates the Homebrew tap
([homebrew-pyhttp](https://github.com/pyhttp2/homebrew-pyhttp): cask + formula;
the tap needs the `RELEASE_TOKEN` secret, a fine-grained PAT with Contents: write
on that repository). The Windows runner installs Inno Setup with Chocolatey when
it is not already there. The in-app updater refuses a release without `SHA256SUMS.txt`,
so keep that step in the workflow. A tag whose version differs from `__version__` is refused,
because the release name, file names and the in-app update check all come from
`__version__`. Alternatively run the workflow by hand from the Actions tab with
"publish" ticked.

### First launch: unsigned app warnings

The builds are not code-signed, so the operating system warns the first time:

- **Windows** shows "Windows protected your PC" (SmartScreen): click **More info → Run anyway**.
- **macOS** shows "Apple could not verify pyhttp is free of malware": click **Done**,
  open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**
  next to pyhttp (right-click → Open no longer works on macOS 15). Or clear the
  quarantine flag once, which also fixes "the app is damaged":
  `xattr -dr com.apple.quarantine /Applications/pyhttp.app`. The in-app updater
  clears it itself after a Homebrew update; a `brew upgrade` run in Terminal does not.

Removing these warnings needs a Windows code-signing certificate and an Apple
Developer ID (with notarization).

## Architecture

```
pyhttp/
  __main__.py     entry point: native window (pywebview) or browser/server mode
  app.py          FastAPI routes (/api/...) + static UI
  db.py           SQLite access (requests, folders, environments, cookies, history, runs)
  runner.py       httpx execution (auth, cookies, redirects, proxy, body types), RunManager, file output
  mock.py         mock servers: route matching and templated answers under /mock/<name>/
  templating.py   {{variable}}, {{prev.*}}, {{data.*}} and {{$dynamic}} substitution
  jsonc.py        strips // and /* */ comments from JSON bodies
  assertions.py   code-free tests and value extraction
  jsengine.py     JavaScript scripts (Postman-compatible pm API) in QuickJS or V8; pm.js is the runtime
  scripts.py      optional Python pre-request / test scripts (py object)
  postman.py      Postman Collection v2.1 and environment import/export
  curl.py         curl command line -> request
  updater.py      in-app updates: download + verify + helper script (Windows), brew upgrade (macOS)
  static/         index.html, style.css, features.css, app.js, editor.js (CodeMirror editors),
                  viewer.js (response viewer), codegen.js (snippets), features.js,
                  vendor/codemirror.js (prebuilt CodeMirror 6 bundle, see packaging/editor)
packaging/        build.py, pyhttp.spec, pyhttp.iss (Windows installer), make_icons.py (desktop builds)
tests/            test_backend.py (API), test_ui.py (Playwright, headless Edge/Chromium)
```

Run the tests with `pip install -r requirements-dev.txt` and `python -m pytest tests -q`.

In `--browser` or `--server` mode the API is documented at
<http://127.0.0.1:8765/api/docs>, so you can drive runs from scripts
(`POST /api/runs`). Window mode uses the same default port when it is free and
falls back to any free one, printing it on start — so mock server URLs stay the
same from launch to launch.

## License

Copyright (c) 2026 pyhttp Dev Team — *From the unknown to the unowned*. pyhttp is **free for everyone** under the
[pyhttp Free License 1.1](LICENSE.md): at home or at work, at a company of any
size, for any purpose including commercial use, with no fees, no seats and no
registration. Unmodified release files may be shared freely, inside a company
or through package managers; selling pyhttp or distributing modified versions
is not allowed, and the name and logo stay with the project. The source code is
published on PyPI as [`pyhttp-app`](https://pypi.org/project/pyhttp-app/)
so it can be inspected and installed with pip; you may modify your own copy but
not distribute the changes. The packaged installer, `.exe`, `.dmg` and `.deb`
builds are published on this repository's
[Releases](https://github.com/pyhttp2/pyhttp/releases) page (the old
`pyhttp-releases` repository is no longer updated; every install checks here).
Bundled open-source components are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Postman is a trademark of Postman, Inc. pyhttp is an independent project and is
not affiliated with or endorsed by Postman, Inc.; the name is used only to
describe compatibility (importing Postman collections, running Postman-style
scripts).

The app itself stays free. If paid extras ever appear, they will be optional
add-ons, services or support under their own terms, never a restriction on who
may use pyhttp.

## Keyboard shortcuts

| Keys            | Action        |
|-----------------|---------------|
| Ctrl/Cmd+Enter  | Send request  |
| Ctrl/Cmd+S      | Save now (autosave is always on) |
| Ctrl/Cmd+N      | New request   |
| Ctrl/Cmd+D      | Duplicate the request |
| Ctrl/Cmd+W      | Close the current tab |
| Ctrl/Cmd+K      | Search the collection |
| Ctrl/Cmd+/      | Comment / uncomment the line(s) in the body |
| Ctrl/Cmd+B      | Beautify the body |
| Tab / Shift+Tab | Indent / outdent in the body |
| Ctrl/Cmd+F      | Search in the response body |
| Ctrl/Cmd+Shift+C | Code snippet |
| Ctrl/Cmd+Alt+C  | Toggle the console |
| Ctrl/Cmd+\      | Show or hide the sidebar |
| ?               | Shortcut help |
| Esc             | Close dialog  |
