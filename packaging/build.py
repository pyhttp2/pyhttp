"""Build the pyhttp desktop app for the current OS.

    python packaging/build.py

Windows -> dist/pyhttp-<version>-windows-x64.exe          (portable, one file)
           dist/pyhttp-<version>-windows-x64-setup.exe    (installer, needs Inno Setup 6.3+ or 7)
macOS   -> dist/pyhttp-<version>-macos-<arm64|x86_64>.dmg   (plus dist/pyhttp.app)
Linux   -> dist/pyhttp-<version>-linux-<amd64|arm64>.deb     (Ubuntu 22.04+ / Debian 12+)

Run it in an environment that has the app requirements plus requirements-build.txt.
PyInstaller cannot cross-compile: build the .exe on Windows, the .dmg on a Mac and the
.deb on Ubuntu/Debian. The Linux build needs PyGObject in the environment (the GTK
bindings pywebview uses): `apt install libgirepository1.0-dev libcairo2-dev pkg-config
gir1.2-gtk-3.0 gir1.2-webkit2-4.1 dpkg-dev` then `pip install "PyGObject<3.51" pycairo`
(PyGObject 3.51+ needs girepository-2.0, which Ubuntu 22.04 does not have). The .deb
depends on the distro's GTK and WebKitGTK packages instead of bundling them.
The installer needs Inno Setup (https://jrsoftware.org/isinfo.php): ISCC.exe is looked up
on PATH, in the ISCC environment variable, in the usual Program Files folders and in the
registry. Without it only the portable .exe is built.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
DIST = ROOT / "dist"
WORK = PKG / "build" / "pyinstaller"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "pyhttp" / "__init__.py").read_text()).group(1)


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def write_build_info() -> None:
    """Bake the GitHub repository (owner/repo) into the app so it can check for new releases.
    Comes from PYHTTP_GITHUB_REPO (set by the GitHub workflow) or the git origin remote."""
    import os

    repo = os.environ.get("PYHTTP_GITHUB_REPO", "").strip()
    if not repo:
        try:
            url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
                                 text=True, check=True).stdout.strip()
            m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
            repo = m.group(1) if m else ""
        except (OSError, subprocess.CalledProcessError):
            repo = ""
    (ROOT / "pyhttp" / "_build.py").write_text(f'GITHUB_REPO = "{repo}"\n', encoding="utf-8")
    print(f"update checks against GitHub repository: {repo or '(none)'}", flush=True)


def build_app() -> None:
    write_build_info()
    run([sys.executable, str(PKG / "make_icons.py")])
    run([sys.executable, "-m", "PyInstaller", str(PKG / "pyhttp.spec"),
         "--noconfirm", "--clean", "--distpath", str(DIST), "--workpath", str(WORK)])


def find_iscc() -> Path | None:
    """Inno Setup's command-line compiler, or None when Inno Setup is not installed."""
    import os

    candidates = [os.environ.get("ISCC", "")]
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        candidates.append(found)
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("LOCALAPPDATA", "") and str(Path(os.environ["LOCALAPPDATA"]) / "Programs")):
        for ver in ("7", "6"):
            if base:
                candidates.append(str(Path(base) / f"Inno Setup {ver}" / "ISCC.exe"))
    try:
        import winreg

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for ver in ("7", "6"):
                for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
                    try:
                        key = winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup {ver}_is1",
                                             0, winreg.KEY_READ | view)
                        loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                        candidates.append(str(Path(loc) / "ISCC.exe"))
                    except OSError:
                        continue
    except ImportError:
        pass
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


def numeric_version(version: str) -> str:
    """'0.2.0-rc1' -> '0.2.0.0' for the Windows version resource (four numbers)."""
    nums = [int("".join(ch for ch in part if ch.isdigit()) or 0) for part in version.split(".")[:4]]
    return ".".join(str(n) for n in (nums + [0, 0, 0, 0])[:4])


def build_installer() -> Path | None:
    """Pack dist/pyhttp-win into the installer with Inno Setup (pyhttp.iss)."""
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup (ISCC.exe) not found: installer skipped. Install it from https://jrsoftware.org/isdl.php "
              "or set ISCC=<path to ISCC.exe>.", flush=True)
        return None
    out = DIST / f"pyhttp-{VERSION}-windows-x64-setup.exe"
    run([str(iscc), "/Qp", f"/DVERSION={VERSION}", f"/DNUMVERSION={numeric_version(VERSION)}",
         f"/DSRC={DIST / 'pyhttp-win'}", f"/DOUT={DIST}", str(PKG / "pyhttp.iss")])
    return out


def package_windows() -> Path:
    src = DIST / "pyhttp-portable.exe"
    out = DIST / f"pyhttp-{VERSION}-windows-x64.exe"
    shutil.copy2(src, out)
    installer = build_installer()
    if installer:
        print(f"Built {installer}  ({installer.stat().st_size / 1024 / 1024:.1f} MB)")
    return out


def sign_macos(app: Path) -> bool:
    """Code-sign with a Developer ID when MACOS_SIGN_IDENTITY is set (see the workflow).
    Without it the app is ad-hoc signed and Gatekeeper warns on first launch."""
    import os

    identity = os.environ.get("MACOS_SIGN_IDENTITY", "").strip()
    if not identity:
        print("MACOS_SIGN_IDENTITY not set: app is not Developer-ID signed", flush=True)
        return False
    run(["codesign", "--force", "--deep", "--options", "runtime", "--timestamp",
         "--entitlements", str(PKG / "entitlements.plist"), "--sign", identity, str(app)])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    return True


def notarize_macos(dmg: Path) -> None:
    """Submit the dmg to Apple's notary service and staple the ticket (needs APPLE_ID,
    APPLE_TEAM_ID and APPLE_APP_PASSWORD, an app-specific password)."""
    import os

    apple_id = os.environ.get("APPLE_ID", "").strip()
    team = os.environ.get("APPLE_TEAM_ID", "").strip()
    pw = os.environ.get("APPLE_APP_PASSWORD", "").strip()
    if not (apple_id and team and pw):
        print("APPLE_ID / APPLE_TEAM_ID / APPLE_APP_PASSWORD not set: dmg is not notarized", flush=True)
        return
    run(["xcrun", "notarytool", "submit", str(dmg), "--apple-id", apple_id, "--team-id", team,
         "--password", pw, "--wait"])
    run(["xcrun", "stapler", "staple", str(dmg)])


def package_macos() -> Path:
    app = DIST / "pyhttp.app"
    arch = platform.machine() or "unknown"
    out = DIST / f"pyhttp-{VERSION}-macos-{arch}.dmg"
    signed = sign_macos(app)
    stage = PKG / "build" / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    run(["ditto", str(app), str(stage / "pyhttp.app")])          # keeps symlinks + signatures
    (stage / "Applications").symlink_to("/Applications")         # drag-to-install target
    if out.exists():
        out.unlink()
    run(["hdiutil", "create", "-volname", f"pyhttp {VERSION}", "-srcfolder", str(stage),
         "-fs", "HFS+", "-format", "UDZO", "-ov", str(out)])
    if signed:
        identity = __import__("os").environ["MACOS_SIGN_IDENTITY"]
        run(["codesign", "--force", "--timestamp", "--sign", identity, str(out)])
        notarize_macos(out)
    return out


# ----------------------------------------------------------------------------- Linux
# GTK, WebKitGTK and their GObject introspection data are loaded at runtime by name, so the
# shared-library scan below cannot see them: list them by hand (24.04 "t64" names first).
LINUX_GUI_DEPENDS = ["libgtk-3-0t64 | libgtk-3-0", "libwebkit2gtk-4.1-0", "gir1.2-gtk-3.0",
                     "gir1.2-webkit2-4.1", "libgirepository-1.0-1"]
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=pyhttp
GenericName=HTTP client
Comment=Local API client with a collection runner (alternative to Postman)
Exec=pyhttp
Icon=pyhttp
Terminal=false
Categories=Development;Network;
Keywords=HTTP;API;REST;Postman;request;
StartupWMClass=pyhttp
"""


def linux_depends(app_dir: Path) -> list[str]:
    """Distro packages that provide the shared libraries the bundle needs but does not ship,
    like dpkg-shlibdeps: NEEDED sonames of every ELF file -> ldconfig path -> dpkg -S package."""
    bundled = {p.name for p in app_dir.rglob("*") if p.is_file()}
    needed: set[str] = set()
    for f in [app_dir / "pyhttp", *app_dir.rglob("*.so*")]:
        if not f.is_file() or f.is_symlink():
            continue
        out = subprocess.run(["objdump", "-p", str(f)], capture_output=True, text=True).stdout
        needed.update(re.findall(r"^\s*NEEDED\s+(\S+)", out, re.M))
    needed -= bundled
    cache: dict[str, str] = {}
    for line in subprocess.run(["ldconfig", "-p"], capture_output=True, text=True).stdout.splitlines():
        m = re.match(r"\s*(\S+) \(.*\) => (\S+)", line)
        if m:
            cache.setdefault(m.group(1), m.group(2))
    missing = sorted(s for s in needed if s not in cache)
    if missing:
        print(f"warning: no system library found for {missing}; the .deb may not list every dependency", flush=True)
    paths = sorted({str(Path(cache[s]).resolve()) for s in needed if s in cache})
    pkgs: set[str] = set()
    if paths:
        out = subprocess.run(["dpkg", "-S", *paths], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if ": /" in line:
                pkgs.add(line.split(":")[0].strip())
    print(f"shared libraries from the distro: {', '.join(sorted(pkgs)) or '(none)'}", flush=True)
    return sorted(pkgs)


def package_linux() -> Path:
    import os

    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    out = DIST / f"pyhttp-{VERSION}-linux-{arch}.deb"
    stage = PKG / "build" / "deb"
    shutil.rmtree(stage, ignore_errors=True)
    app = stage / "usr" / "lib" / "pyhttp"
    shutil.copytree(DIST / "pyhttp-linux", app, symlinks=True)
    (stage / "usr" / "bin").mkdir(parents=True)
    os.symlink("../lib/pyhttp/pyhttp", stage / "usr" / "bin" / "pyhttp")
    apps = stage / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "pyhttp.desktop").write_text(DESKTOP_ENTRY, encoding="utf-8")
    for size in (16, 32, 48, 64, 128, 256, 512):
        src = PKG / "build" / f"icon-{size}.png"
        if src.is_file():
            dst = stage / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
            dst.mkdir(parents=True)
            shutil.copy2(src, dst / "pyhttp.png")
    doc = stage / "usr" / "share" / "doc" / "pyhttp"
    doc.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE.md", doc / "copyright")
    shutil.copy2(ROOT / "THIRD-PARTY-NOTICES.md", doc / "THIRD-PARTY-NOTICES.md")
    depends = sorted(set(linux_depends(app)) | set(LINUX_GUI_DEPENDS))
    size_kb = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file()) // 1024
    control = (
        f"Package: pyhttp\nVersion: {VERSION}\nSection: devel\nPriority: optional\nArchitecture: {arch}\n"
        f"Maintainer: pyhttp Dev Team <dev@pyhttp.com>\nInstalled-Size: {size_kb}\nDepends: {', '.join(depends)}\n"
        "Homepage: https://pyhttp.com\n"
        "Description: Local API client with a collection runner (alternative to Postman)\n"
        " Save requests with named bodies, environments, tests and scripts, run\n"
        " collections and keep every response as files. Everything stays on your\n"
        " machine. Free for everyone under the pyhttp Free License (see copyright).\n"
    )
    (stage / "DEBIAN").mkdir()
    (stage / "DEBIAN" / "control").write_text(control, encoding="utf-8")
    for p in stage.rglob("*"):   # dpkg wants readable files, executable binaries and 0755 directories
        if p.is_symlink():
            continue
        os.chmod(p, 0o755 if p.is_dir() or os.access(p, os.X_OK) else 0o644)
    out.unlink(missing_ok=True)
    run(["dpkg-deb", "--build", "--root-owner-group", "-Zxz", str(stage), str(out)])
    return out


def main() -> None:
    if sys.platform not in ("win32", "darwin") and not sys.platform.startswith("linux"):
        sys.exit("This script builds for Windows, macOS or Linux.")
    build_app()
    out = package_windows() if sys.platform == "win32" else package_macos() if sys.platform == "darwin" else package_linux()
    size = out.stat().st_size / 1024 / 1024
    print(f"\nBuilt {out}  ({size:.1f} MB)")


if __name__ == "__main__":
    main()
