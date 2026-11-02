"""In-app updates for the packaged desktop builds.

Linux (.deb) and other installs get the download plus the command to run.

Windows (installer or portable .exe): download the new release asset, verify it against
the release's SHA256SUMS.txt, then hand over to a small .cmd helper that waits for pyhttp
to exit, installs (quietly) or swaps the .exe, and starts pyhttp again.

macOS (installed with Homebrew): run `brew update` and `brew upgrade --cask pyhttp` with
live output, then restart the app. Other installs (plain .dmg, no Homebrew, running from
source) get the commands to run instead; see `info()`.
"""
from __future__ import annotations

import hashlib
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from . import USER_AGENT

FROZEN = bool(getattr(sys, "frozen", False))
CASK = "pyhttp"
TAP = "pyhttp2/pyhttp"
TAP_URL = "https://github.com/pyhttp2/homebrew-pyhttp"
SUMS_FILE = "SHA256SUMS.txt"
SELF_UPDATE_KINDS = ("installer", "portable", "brew")

# Set by __main__: closes the native window (or stops the server) so the helper can replace the files.
quit_hook: Callable[[], None] | None = None


# ----------------------------------------------------------------- where are we installed?
def _exe() -> Path:
    return Path(sys.executable).resolve()


def is_onefile() -> bool:
    """PyInstaller one-file build: a bootloader process unpacks to a _MEIxxxx temp folder."""
    return FROZEN and Path(getattr(sys, "_MEIPASS", "")).name.startswith("_MEI")


def brew_path() -> str | None:
    """Homebrew's `brew`, also when launched from Finder (PATH is minimal there)."""
    for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew", "/home/linuxbrew/.linuxbrew/bin/brew"):
        if os.path.isfile(p):
            return p
    return shutil.which("brew")


def brew_caskroom() -> Path | None:
    """The cask's Caskroom folder when pyhttp was installed with Homebrew."""
    for p in ("/opt/homebrew/Caskroom", "/usr/local/Caskroom"):
        d = Path(p) / CASK
        if d.is_dir():
            return d
    return None


def app_bundle() -> Path | None:
    """The .app that contains the running executable (macOS)."""
    for parent in _exe().parents:
        if parent.suffix == ".app":
            return parent
    return None


def deb_arch(machine: str | None = None) -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(machine or _platform.machine(), machine or _platform.machine())


def install_kind() -> str:
    """source | installer | portable | brew | app | deb | other."""
    if not FROZEN:
        return "source"
    if sys.platform.startswith("win"):
        return "installer" if any(_exe().parent.glob("unins*.exe")) else "portable"
    if sys.platform == "darwin":
        return "brew" if brew_caskroom() else "app"
    if sys.platform.startswith("linux"):
        return "deb" if str(_exe()).startswith("/usr/lib/pyhttp/") else "other"
    return "other"


def _commands(kind: str, latest: str | None = None) -> tuple[str, str, list[str]]:
    """(title, note, commands) shown in the update dialog when pyhttp cannot update itself."""
    if kind == "deb":
        return ("Install the update", "Download the .deb below, then run this in the folder where it was saved:",
                [f"sudo apt install ./pyhttp-{latest or '<version>'}-linux-{deb_arch()}.deb"])
    app = str(app_bundle() or "/Applications/pyhttp.app")
    unquarantine = f"xattr -dr com.apple.quarantine {app}"
    if kind == "brew":
        return ("Or update from the terminal", "", ["brew update", f"brew upgrade --cask {CASK}", unquarantine])
    if kind == "app":
        tap = [f"brew tap {TAP} {TAP_URL}", f"brew install --cask {TAP}/{CASK}", unquarantine]
        if brew_path():
            return ("Switch to Homebrew (then pyhttp updates itself)",
                    f"Quit pyhttp and move {app} to the Trash first; your data in ~/Library/Application Support/pyhttp is kept.", tap)
        return ("Install with Homebrew (then pyhttp updates itself)",
                f"Installs Homebrew first. Quit pyhttp and move {app} to the Trash before the install; your data is kept.",
                ['/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"', *tap])
    if kind == "source":
        return ("Update the source checkout", "", ["git pull", "pip install -r requirements.txt"])
    return ("", "", [])


def info(latest: str | None = None) -> dict[str, Any]:
    """How pyhttp is installed and whether it can update itself (for /api/meta and the dialog)."""
    kind = install_kind()
    title, note, commands = _commands(kind, latest)
    return {
        "kind": kind, "platform": sys.platform, "self_update": kind in SELF_UPDATE_KINDS,
        "brew": bool(brew_path()) if sys.platform == "darwin" else False,
        "exe": str(_exe()) if FROZEN else "", "commands": commands, "commands_title": title, "commands_note": note,
    }


# ----------------------------------------------------------------- release assets
def pick_asset(assets: list[dict[str, Any]], kind: str | None = None, plat: str | None = None,
               machine: str | None = None) -> dict[str, Any] | None:
    """The release file for this computer: installer or portable .exe on Windows, the .dmg
    for this CPU on macOS, the .deb for this CPU on Linux. Falls back to whatever file exists."""
    plat = plat or sys.platform
    kind = kind or install_kind()
    named = [(a, str(a.get("name") or "").lower()) for a in assets]
    if plat.startswith("win"):
        setup = next((a for a, n in named if "windows" in n and n.endswith("-setup.exe")), None)
        portable = next((a for a, n in named if "windows" in n and n.endswith(".exe") and not n.endswith("-setup.exe")), None)
        return (setup or portable) if kind == "installer" else (portable or setup)
    if plat == "darwin":
        arch = "x86_64" if (machine or _platform.machine()) == "x86_64" else "arm64"
        dmgs = [a for a, n in named if "macos" in n and n.endswith(".dmg")]
        return next((a for a in dmgs if arch in str(a.get("name") or "").lower()), dmgs[0] if dmgs else None)
    debs = [a for a, n in named if "linux" in n and n.endswith(".deb")]
    arch = deb_arch(machine)
    return next((a for a in debs if arch in str(a.get("name") or "").lower()), debs[0] if debs else None)


def parse_sha256sums(text: str) -> dict[str, str]:
    """`<sha256>  <file>` lines (sha256sum format) -> {file name: hash}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[Path(m.group(2).replace("\\", "/")).name] = m.group(1).lower()
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------- Windows helper script
def windows_script(kind: str, downloaded: Path, exe: Path, pids: list[int], log: Path) -> str:
    """The .cmd that finishes the update after pyhttp has exited (pids: the app processes)."""
    wait = ",".join(str(p) for p in pids) or str(os.getpid())
    lines = [
        "@echo off",
        "rem pyhttp update helper: waits for pyhttp to exit, then installs the new version",
        f'powershell -NoProfile -NonInteractive -Command "Wait-Process -Id {wait} -ErrorAction SilentlyContinue"',
    ]
    if kind == "installer":
        lines += [
            f'"{downloaded}" /VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /LOG="{log}"',
            "exit /b %errorlevel%",
        ]
    else:
        old = Path(str(exe) + ".old")
        lines += [
            "set n=0",
            ":retry",
            f'move /y "{exe}" "{old}" >nul 2>&1',
            "if not errorlevel 1 goto moved",
            "set /a n+=1",
            "if %n% geq 30 goto fail",
            "timeout /t 1 /nobreak >nul",
            "goto retry",
            ":moved",
            f'copy /y "{downloaded}" "{exe}" >nul',
            "if errorlevel 1 goto restore",
            f'start "" "{exe}"',
            f'del /q "{old}" >nul 2>&1',
            f'del /q "{downloaded}" >nul 2>&1',
            "exit /b 0",
            ":restore",
            f'move /y "{old}" "{exe}" >nul 2>&1',
            ":fail",
            f'start "" "{exe}"',
            "exit /b 1",
        ]
    return "\r\n".join(lines) + "\r\n"


def cleanup(data_dir: Path) -> None:
    """At start: remove leftovers of a previous update (the old .exe and downloaded files)."""
    if not FROZEN:
        return
    try:
        if sys.platform.startswith("win"):
            old = Path(str(_exe()) + ".old")
            if old.exists():
                old.unlink()
        shutil.rmtree(data_dir / "updates", ignore_errors=True)
    except OSError:
        pass


# ----------------------------------------------------------------- the updater
class Updater:
    """Runs one update at a time in a background thread; `status()` is polled by the UI."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir / "updates"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {"state": "idle", "progress": None, "message": "", "log": [], "error": None}

    # ---- status
    def status(self) -> dict[str, Any]:
        with self._lock:
            return {**self._status, "log": list(self._status["log"])}

    def _set(self, state: str, progress: float | None = None, message: str = "", error: str | None = None) -> None:
        with self._lock:
            self._status.update({"state": state, "progress": progress, "message": message, "error": error})
        print(f"update: {state} {message}".rstrip(), flush=True)

    def _log(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        with self._lock:
            log = self._status["log"]
            log.append(line)
            del log[:-200]
            self._status["message"] = line

    # ---- start
    def start(self, release: dict[str, Any]) -> dict[str, Any]:
        """release: the /api/update-check answer (needs `assets` and `latest`)."""
        kind = install_kind()
        if kind not in SELF_UPDATE_KINDS:
            raise RuntimeError("this installation cannot update itself; use the download or the commands shown")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("an update is already running")
        assets = release.get("assets") or []
        if kind == "brew":
            target = self._run_brew
            args: tuple[Any, ...] = (str(release.get("latest") or ""),)
        else:
            asset = pick_asset(assets, kind)
            if not asset or not asset.get("url"):
                raise RuntimeError("this release has no download for Windows")
            sums = next((a for a in assets if str(a.get("name") or "") == SUMS_FILE), None)
            if not sums or not sums.get("url"):
                raise RuntimeError(f"this release has no {SUMS_FILE}, so the download cannot be verified; use the download instead")
            target = self._run_windows
            args = (kind, asset, sums)
        self._status = {"state": "starting", "progress": None, "message": "Starting…", "log": [], "error": None}
        self._thread = threading.Thread(target=self._guard, args=(target, *args), daemon=True, name="pyhttp-update")
        self._thread.start()
        return self.status()

    def _guard(self, fn: Callable[..., None], *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            self._set("error", None, str(exc), error=str(exc))

    # ---- Windows: download, verify, hand over to the helper
    def _download(self, asset: dict[str, Any]) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        dest = self.dir / Path(str(asset["name"])).name
        part = dest.with_suffix(dest.suffix + ".part")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
        done = 0
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True) as client, \
                client.stream("GET", str(asset["url"]), headers=headers) as r:
            if r.status_code != 200:
                raise RuntimeError(f"download failed: GitHub answered {r.status_code}")
            total = int(r.headers.get("content-length") or asset.get("size") or 0)
            last = 0.0
            with part.open("wb") as f:
                for chunk in r.iter_bytes(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if total and time.time() - last > 0.2:
                        last = time.time()
                        self._set("downloading", done / total, f"Downloading {asset['name']}: {done // 1048576} of {total // 1048576} MB")
        if dest.exists():
            dest.unlink()
        part.rename(dest)
        return dest

    def _run_windows(self, kind: str, asset: dict[str, Any], sums: dict[str, Any]) -> None:
        self._set("downloading", 0.0, f"Downloading {asset['name']}…")
        path = self._download(asset)
        self._set("verifying", None, "Verifying the download…")
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(str(sums["url"]), headers={"User-Agent": USER_AGENT})
        expected = parse_sha256sums(r.text if r.status_code == 200 else "").get(str(asset["name"]))
        if not expected:
            raise RuntimeError(f"{SUMS_FILE} of the release does not list {asset['name']}")
        actual = sha256_of(path)
        if actual != expected:
            path.unlink(missing_ok=True)
            raise RuntimeError("the downloaded file is damaged (checksum mismatch); try again later")
        self._set("installing", None, "Starting the installer…" if kind == "installer" else "Replacing the program file…")
        pids = [os.getpid()] + ([os.getppid()] if is_onefile() else [])
        script = self.dir / "apply-update.cmd"
        script.write_text(windows_script(kind, path, _exe(), pids, self.dir / "setup.log"), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["cmd.exe", "/c", str(script)], cwd=str(self.dir), creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set("restarting", None, "pyhttp closes now and opens again on the new version.")
        _quit_later()

    # ---- macOS: Homebrew
    def _run_logged(self, cmd: list[str], env: dict[str, str]) -> None:
        self._log("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buf = b""
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(512)
            if not chunk:
                break
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for line in lines:
                self._log(line.decode("utf-8", errors="replace"))
        if buf:
            self._log(buf.decode("utf-8", errors="replace"))
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} failed (exit code {rc}); see the output above")

    def _run_brew(self, latest: str) -> None:
        brew = brew_path()
        if not brew:
            raise RuntimeError("Homebrew (brew) was not found")
        env = {**os.environ, "PATH": f"{Path(brew).parent}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_INSTALL_CLEANUP": "1",
               "HOMEBREW_COLOR": "0", "NO_COLOR": "1", "NONINTERACTIVE": "1"}
        self._set("updating", None, "Updating Homebrew…")
        self._run_logged([brew, "update"], env)
        self._set("upgrading", None, f"Upgrading {CASK}…")
        self._run_logged([brew, "upgrade", "--cask", CASK], env)
        room = brew_caskroom()
        if latest and room and not (room / latest).is_dir():
            raise RuntimeError(f"Homebrew does not offer {latest} yet (the cask is updated shortly after a release); try again later")
        app = app_bundle() or Path("/Applications/pyhttp.app")
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(app)], env=env, check=False,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._set("restarting", None, "pyhttp closes now and opens again on the new version.")
        relaunch = f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; open "{app}"'
        subprocess.Popen(["/bin/sh", "-c", relaunch], start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _quit_later()


def _quit_later(delay: float = 1.5) -> None:
    """Give the UI time to read the final status, then close the window; hard-exit as a fallback."""
    def go() -> None:
        time.sleep(delay)
        if quit_hook:
            try:
                quit_hook()
            except Exception as exc:  # noqa: BLE001
                print(f"update: quit hook failed: {exc}", flush=True)
        time.sleep(8)
        os._exit(0)
    threading.Thread(target=go, daemon=True, name="pyhttp-quit").start()
