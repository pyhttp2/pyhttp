"""Secret values in the operating system's credential store instead of the project database.

Opt-in (Settings → General → "Keep 🔒 values in the system keychain"). With it on, the value of
every variable marked secret is written to

    Windows   Credential Manager (generic credentials named ``pyhttp:<name>``)
    macOS     the login keychain, through the ``security`` tool (service ``pyhttp``)
    Linux     the Secret Service (GNOME Keyring, KWallet…), through ``secret-tool``

and the database keeps only a marker. No extra Python package is needed. Values are stored as
base64 text in pieces of at most ``CHUNK`` characters, because Windows limits one credential to
2560 bytes and a token can be longer than that.

``db.py`` is the only caller: it decides which variable goes where and under which name.
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import threading
from typing import Any

SERVICE = "pyhttp"
CHUNK = 1600   # characters of base64 per stored entry
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SecretStoreError(Exception):
    pass


# ---------------------------------------------------------------- backends
class WindowsBackend:
    label = "Windows Credential Manager"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        self._ctypes, self._cred = ctypes, CREDENTIAL
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        self._api = api

    @staticmethod
    def _target(name: str) -> str:
        return f"{SERVICE}:{name}"

    def set(self, name: str, value: str) -> None:
        ctypes = self._ctypes
        data = value.encode("ascii")
        blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data) if data else None
        cred = self._cred()
        cred.Type = 1            # CRED_TYPE_GENERIC
        cred.Persist = 2         # CRED_PERSIST_LOCAL_MACHINE: this user, this computer
        cred.TargetName = self._target(name)
        cred.UserName = SERVICE
        cred.Comment = "A secret variable of pyhttp"
        cred.CredentialBlobSize = len(data)
        cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)) if blob else None
        if not self._api.CredWriteW(ctypes.byref(cred), 0):
            raise SecretStoreError(f"Credential Manager refused the value (error {ctypes.get_last_error()})")

    def get(self, name: str) -> str | None:
        ctypes = self._ctypes
        ptr = ctypes.POINTER(self._cred)()
        if not self._api.CredReadW(self._target(name), 1, 0, ctypes.byref(ptr)):
            return None
        try:
            cred = ptr.contents
            return bytes(cred.CredentialBlob[:cred.CredentialBlobSize]).decode("ascii", "replace")
        finally:
            self._api.CredFree(ptr)

    def delete(self, name: str) -> None:
        self._api.CredDeleteW(self._target(name), 1, 0)


class _ToolBackend:
    tool = ""

    def __init__(self) -> None:
        self._exe = shutil.which(self.tool)
        if not self._exe:
            raise SecretStoreError(f"The “{self.tool}” tool was not found")

    def _run(self, args: list[str], stdin: str | None = None) -> tuple[int, str]:
        try:
            proc = subprocess.run([self._exe, *args], input=stdin, capture_output=True, text=True, timeout=20,
                                  creationflags=_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SecretStoreError(f"{self.tool} failed: {exc}") from exc
        return proc.returncode, (proc.stdout or "").strip()


class MacBackend(_ToolBackend):
    label = "macOS Keychain"
    tool = "security"

    def set(self, name: str, value: str) -> None:
        # through the tool's own prompt (-i), so the value never shows in the process list;
        # name and value are plain ASCII without quotes or spaces (see _safe and base64)
        code, _ = self._run(["-i"], stdin=f'add-generic-password -U -s {SERVICE} -a "{name}" -w "{value}"\n')
        if code != 0 or self.get(name) != value:
            raise SecretStoreError("The keychain did not accept the value (is the login keychain unlocked?)")

    def get(self, name: str) -> str | None:
        code, out = self._run(["find-generic-password", "-s", SERVICE, "-a", name, "-w"])
        return out if code == 0 else None

    def delete(self, name: str) -> None:
        self._run(["delete-generic-password", "-s", SERVICE, "-a", name])


class LinuxBackend(_ToolBackend):
    label = "Secret Service (GNOME Keyring / KWallet)"
    tool = "secret-tool"

    def set(self, name: str, value: str) -> None:
        code, _ = self._run(["store", "--label", f"{SERVICE} {name}", "service", SERVICE, "account", name], stdin=value)
        if code != 0:
            raise SecretStoreError("The Secret Service did not accept the value (is a keyring running and unlocked?)")

    def get(self, name: str) -> str | None:
        code, out = self._run(["lookup", "service", SERVICE, "account", name])
        return out if code == 0 and out else None

    def delete(self, name: str) -> None:
        self._run(["clear", "service", SERVICE, "account", name])


class MemoryBackend:
    """For tests: a dict."""
    label = "memory"

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self.items[name] = value

    def get(self, name: str) -> str | None:
        return self.items.get(name)

    def delete(self, name: str) -> None:
        self.items.pop(name, None)


# ------------------------------------------------------------------- store
_lock = threading.RLock()
_backend: Any = None
_backend_error = ""
_resolved = False
_cache: dict[str, str | None] = {}


def use(backend: Any) -> None:
    """Install a backend (tests), or None to pick the platform's own again."""
    global _backend, _backend_error, _resolved
    with _lock:
        _backend, _backend_error, _resolved = backend, "", backend is not None
        _cache.clear()


def _get_backend() -> Any:
    global _backend, _backend_error, _resolved
    with _lock:
        if not _resolved:
            _resolved = True
            try:
                if sys.platform.startswith("win"):
                    _backend = WindowsBackend()
                elif sys.platform == "darwin":
                    _backend = MacBackend()
                else:
                    _backend = LinuxBackend()
            except Exception as exc:  # noqa: BLE001 - reported to the user by available()
                _backend, _backend_error = None, str(exc)
        return _backend


def label() -> str:
    b = _get_backend()
    return b.label if b else ""


def available() -> tuple[bool, str]:
    """(usable, message). Writes, reads back and removes a test entry, so a keyring that is
    installed but locked or not running is reported before any secret is moved into it."""
    b = _get_backend()
    if b is None:
        hint = " Install it with your package manager (libsecret-tools)." if not sys.platform.startswith(("win", "darwin")) else ""
        return False, (_backend_error or "No system keychain on this computer") + hint
    try:
        b.set("probe", "cHJvYmU=")
        ok = b.get("probe") == "cHJvYmU="
        b.delete("probe")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return (True, b.label) if ok else (False, f"{b.label} did not return the test value")


def put(name: str, value: str) -> None:
    """Store ``value`` under ``name``; raises SecretStoreError when the keychain cannot be used."""
    b = _get_backend()
    if b is None:
        raise SecretStoreError(_backend_error or "No system keychain on this computer")
    text = base64.b64encode(value.encode("utf-8")).decode("ascii")
    parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or [""]
    with _lock:
        old = _count(b, name)
        try:
            b.set(name, f"{len(parts)}|{parts[0]}")
            for i, part in enumerate(parts[1:], start=1):
                b.set(f"{name}#{i}", part)
        except SecretStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SecretStoreError(str(exc)) from exc
        for i in range(len(parts), old):   # a shorter value than before: drop the pieces left over
            b.delete(f"{name}#{i}")
        _cache[name] = value


def _count(b: Any, name: str) -> int:
    try:
        head = b.get(name)
        return int(head.split("|", 1)[0]) if head and "|" in head else 0
    except Exception:  # noqa: BLE001
        return 0


def get(name: str) -> str | None:
    """The value stored under ``name``, or None when there is none (or the keychain is unreachable)."""
    with _lock:
        if name in _cache:
            return _cache[name]
        b = _get_backend()
        value: str | None = None
        if b is not None:
            try:
                head = b.get(name)
                if head and "|" in head:
                    n, first = head.split("|", 1)
                    parts = [first] + [b.get(f"{name}#{i}") or "" for i in range(1, int(n))]
                    value = base64.b64decode("".join(parts)).decode("utf-8")
            except Exception:  # noqa: BLE001 - an unreadable entry is a missing value, not a crash
                value = None
        _cache[name] = value
        return value


def delete(name: str) -> None:
    with _lock:
        _cache.pop(name, None)
        b = _get_backend()
        if b is None:
            return
        try:
            for i in range(1, _count(b, name)):
                b.delete(f"{name}#{i}")
            b.delete(name)
        except Exception:  # noqa: BLE001 - nothing useful to do about a failed cleanup
            pass
