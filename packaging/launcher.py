"""Entry point for the packaged app (PyInstaller needs a script, not `-m pyhttp`)."""
import os
import sys


def _use_system_gtk() -> None:
    """Linux: the .deb relies on the distro's GTK, WebKitGTK and GLib (see pyhttp.spec), but
    PyInstaller's gi runtime hooks point GTK at folders inside the bundle that do not exist.
    Undo that before pywebview imports gi."""
    if not sys.platform.startswith("linux") or not getattr(sys, "frozen", False):
        return
    for var in ("GI_TYPELIB_PATH", "GTK_DATA_PREFIX", "GTK_EXE_PREFIX", "GTK_PATH", "PANGO_LIBDIR", "PANGO_SYSCONFDIR",
                "GDK_PIXBUF_MODULE_FILE", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR", "FONTCONFIG_FILE", "GST_PLUGIN_PATH"):
        os.environ.pop(var, None)
    bundle = getattr(sys, "_MEIPASS", "")
    dirs = [d for d in os.environ.get("XDG_DATA_DIRS", "").split(":") if d and not d.startswith(bundle)]
    if dirs:
        os.environ["XDG_DATA_DIRS"] = ":".join(dirs)
    else:
        os.environ.pop("XDG_DATA_DIRS", None)


_use_system_gtk()

from pyhttp.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
