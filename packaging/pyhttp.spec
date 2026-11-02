# PyInstaller spec for pyhttp.  Build with:  python packaging/build.py
#   Windows -> dist/pyhttp-portable.exe   (single portable file, no console window)
#              dist/pyhttp-win/      (one-folder build, packed into the installer by pyhttp.iss)
#   macOS   -> dist/pyhttp.app       (build.py then wraps it in a .dmg)
#   Linux   -> dist/pyhttp-linux/    (one folder; build.py packs it into a .deb)
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821  (SPECPATH is set by PyInstaller)
ICONS = ROOT / "packaging" / "build"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "pyhttp" / "__init__.py").read_text()).group(1)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "pyhttp" / "static"), "pyhttp/static"), (str(ROOT / "pyhttp" / "pm.js"), "pyhttp")]
    + [(str(ROOT / f), ".") for f in ("LICENSE.md", "THIRD-PARTY-NOTICES.md")
       if (ROOT / f).is_file()],
    # uvicorn picks its protocol/loop implementations at runtime by name
    hiddenimports=collect_submodules("uvicorn"),
    excludes=["tkinter", "unittest", "pydoc", "test", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="pyhttp",
        console=False,
        icon=str(ICONS / "icon.icns"),
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="pyhttp")
    app = BUNDLE(
        coll,
        name="pyhttp.app",
        icon=str(ICONS / "icon.icns"),
        bundle_identifier="dev.pyhttp.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "pyhttp",
            "CFBundleDisplayName": "pyhttp",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,   # follow light/dark mode
            # the UI is served from http://127.0.0.1 inside the app
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
elif sys.platform.startswith("linux"):
    # One folder. System shared libraries (GTK, WebKitGTK, GLib, OpenSSL, ...) are left out on
    # purpose: the .deb depends on the distro packages instead (build.py works them out), which
    # keeps the package small and lets WebKit get the distro's security updates. Python's own
    # files stay in, wherever the interpreter was installed.
    SYSTEM_LIB_DIRS = ("/usr/lib", "/lib", "/usr/lib64", "/lib64")
    a.binaries = [(name, src, kind) for name, src, kind in a.binaries
                  if not (src.startswith(SYSTEM_LIB_DIRS) and "python" not in src.lower())]
    # ... and neither are the GTK data files PyInstaller's gi hooks collect (icon theme, pixbuf
    # loader cache, typelibs, GIO modules, schemas): a half-bundled GTK crashes. launcher.py
    # also clears the environment variables those hooks set, so GTK uses the distro's files.
    GTK_DATA_PREFIXES = ("share/", "share\\", "lib/", "etc/", "gi_typelibs", "gio_modules")
    a.datas = [(name, src, kind) for name, src, kind in a.datas if not name.startswith(GTK_DATA_PREFIXES)]
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="pyhttp",
        console=True,       # no "windowed" flag on Linux; output goes to the terminal when there is one
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="pyhttp-linux")
else:
    # portable: one self-extracting file (build.py renames it to pyhttp-<version>-windows-x64.exe)
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="pyhttp-portable",
        console=False,
        icon=str(ICONS / "icon.ico"),
        upx=False,          # UPX-packed executables trigger more antivirus false positives
    )
    # installer: one folder (starts faster, fewer antivirus false positives); pyhttp.iss packs it
    exe_dir = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="pyhttp",
        console=False,
        icon=str(ICONS / "icon.ico"),
        upx=False,
    )
    coll = COLLECT(exe_dir, a.binaries, a.datas, name="pyhttp-win")
