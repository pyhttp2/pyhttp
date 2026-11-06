"""Command line entry point.

    python -m pyhttp                 # native desktop window (default)
    python -m pyhttp --browser       # serve the UI and open it in the browser
    python -m pyhttp --server        # serve only, no UI (for scripting via the API)
    python -m pyhttp run [targets]   # run requests headless and report (for CI); see `run --help`
    python -m pyhttp mcp             # MCP server on stdio, so AI tools can manage the collection
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__, updater
from .app import create_app

FROZEN = bool(getattr(sys, "frozen", False))  # running from a packaged .exe / .app


def _user_data_dir() -> Path:
    """Per-user, writable data folder for the packaged app."""
    if sys.platform.startswith("win"):
        import os
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "pyhttp"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pyhttp"
    import os
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "pyhttp"


# From source: ./data next to the project (unchanged). Packaged: the per-user folder,
# because the app's own files are read-only (macOS .app) or temporary (onefile .exe).
DEFAULT_DATA_DIR = _user_data_dir() if FROZEN else Path(__file__).resolve().parent.parent / "data"


def _redirect_output_when_windowed(data_dir: Path) -> None:
    """A windowed build has no console (stdout/stderr are None); log to a file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    log = open(data_dir / "pyhttp.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _preferred_port(host: str, preferred: int) -> int:
    """The usual port when it is free, any free one otherwise. The window mode used
    to always take a random port; keeping it steady means mock server URLs
    (http://host:<port>/mock/...) stay the same from launch to launch."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, preferred))
        except OSError:
            return _free_port(host)
        return preferred


def _start_server_thread(app, host: str, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if not thread.is_alive() or time.time() > deadline:
            raise RuntimeError(f"Could not start the local server on {host}:{port}")
        time.sleep(0.05)
    return server


class NativeApi:
    """Methods callable from JavaScript as `window.pywebview.api.<name>(...)`."""

    def __init__(self) -> None:
        self._window = None  # private: pywebview does not expose _attrs to JS

    @staticmethod
    def _types_for(filename: str) -> tuple[str, ...]:
        ext = Path(filename).suffix.lower()
        named = {
            ".json": "JSON files (*.json)", ".html": "HTML files (*.html)", ".xml": "XML files (*.xml)",
            ".txt": "Text files (*.txt)", ".csv": "CSV files (*.csv)", ".png": "PNG images (*.png)",
            ".jpg": "JPEG images (*.jpg)", ".pdf": "PDF files (*.pdf)",
        }
        first = named.get(ext)
        return (first, "All files (*.*)") if first else ("All files (*.*)",)

    def save_file(self, content: str, filename: str) -> str | None:
        import webview

        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=filename, file_types=self._types_for(filename),
        )
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        Path(path).write_text(content, encoding="utf-8")
        return path

    def save_file_b64(self, content_b64: str, filename: str) -> str | None:
        """Save binary content (base64 from the UI), e.g. an image response."""
        import base64
        import webview

        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=filename, file_types=self._types_for(filename),
        )
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        Path(path).write_bytes(base64.b64decode(content_b64))
        return path

    def open_file(self, kind: str = "json") -> dict | None:
        import webview

        types = ("Variables files (*.csv;*.json)", "All files (*.*)") if kind == "data" else \
            ("Collections, OpenAPI, HAR or cURL (*.json;*.yaml;*.yml;*.har;*.txt;*.sh)", "All files (*.*)")
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=types)
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        return {"name": Path(path).name, "content": Path(path).read_text(encoding="utf-8")}

    def pick_file(self) -> str | None:
        """Choose a file and return its path (for file fields and binary bodies)."""
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("All files (*.*)",))
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    def open_url(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        webbrowser.open(url)
        return True


def run_window(app, host: str, port: int, debug: bool) -> bool:
    """Open the native window. Returns False if no native webview is available."""
    try:
        import webview
    except ImportError:
        print("pywebview is not installed; falling back to the browser.", file=sys.stderr)
        return False

    port = _preferred_port(host, port)
    from . import db  # the window background matches the saved theme (no flash)
    background = "#f6f7f9" if db.get_setting("theme") == "light" else "#111418"
    server = _start_server_thread(app, host, port)
    print(f"pyhttp window open (API at http://{host}:{port}/api/docs)", flush=True)
    api = NativeApi()
    window = webview.create_window(
        f"pyhttp {__version__}", f"http://{host}:{port}/", js_api=api,
        width=1440, height=900, min_size=(960, 600), background_color=background,
        text_select=True,
    )
    api._window = window
    updater.quit_hook = window.destroy   # the in-app updater closes the window when the new version is ready
    try:
        webview.start(debug=debug, private_mode=False)
    except Exception as exc:  # noqa: BLE001 - missing WebView2 / WebKitGTK etc.
        print(f"Native window unavailable ({exc}); falling back to the browser.", file=sys.stderr)
        server.should_exit = True
        return False
    server.should_exit = True
    return True


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from . import cli
        sys.exit(cli.main(sys.argv[2:], DEFAULT_DATA_DIR))
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        from . import mcp
        mcp.main(sys.argv[2:], DEFAULT_DATA_DIR)
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("projects", "doctor"):
        from . import tools
        fn = tools.projects_main if sys.argv[1] == "projects" else tools.doctor_main
        sys.exit(fn(sys.argv[2:], DEFAULT_DATA_DIR))
    parser = argparse.ArgumentParser(prog="pyhttp", description="Local API client (alternative to Postman)",
                                     epilog="Headless runs for CI: pyhttp run --help · workspaces: pyhttp projects --help · checks: pyhttp doctor")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="open the UI in your web browser")
    mode.add_argument("--server", action="store_true", help="run the API server only, no UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765,
                        help="port to listen on (default: 8765; a free port is used when it is taken)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"where the database and outputs live (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--debug", action="store_true", help="enable devtools in the native window")
    # parse_known_args: macOS may pass a "-psn_…" argument when launching an .app
    args, _unknown = parser.parse_known_args()

    _redirect_output_when_windowed(args.data_dir)
    updater.cleanup(args.data_dir)   # leftovers of a previous in-app update
    app = create_app(args.data_dir)

    if not args.browser and not args.server:
        if run_window(app, args.host, args.port, args.debug):
            return

    url = f"http://{args.host}:{args.port}/"
    print(f"pyhttp running at {url}  (data: {args.data_dir.resolve()})  Ctrl+C to stop")
    if not args.server:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
