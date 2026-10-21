"""Shared constants and platform/cache path helpers."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

APP_NAME = "pgportable"
APP_VERSION = "0.1.0"

#: ``"latest"`` makes the builder resolve the newest version each source offers.
DEFAULT_PG_VERSION = "latest"

DEFAULT_PORT = 5432
DEFAULT_SUPERUSER = "postgres"
DEFAULT_DB = "postgres"
DEFAULT_HOST = "127.0.0.1"

#: Locale preference for ``initdb``, in order. C.UTF-8 is requested; the plain
#: "C" locale is the last-resort fallback for platforms that reject C.UTF-8.
LOCALE_PREFERENCE = ("C.UTF-8", "C")

#: Major version from which the builtin locale provider understands C.UTF-8.
BUILTIN_LOCALE_MIN_MAJOR = 17

MARKER_FILE = ".pgportable.json"


def is_frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def current_platform() -> str:
    """Return a normalized ``<os>-<arch>`` identifier."""
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        if machine in ("amd64", "x86_64"):
            return "windows-x64"
        if machine in ("arm64", "aarch64"):
            return "windows-arm64"
        return "windows-x86"
    if sys.platform.startswith("linux"):
        if machine in ("x86_64", "amd64"):
            return "linux-amd64"
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        return f"linux-{machine}"
    if sys.platform == "darwin":
        return "macos-x64" if machine == "x86_64" else "macos-arm64"
    return f"{sys.platform}-{machine}"


def exe_suffix() -> str:
    return ".exe" if os.name == "nt" else ""


def cache_root(override: str | os.PathLike[str] | None = None) -> Path:
    """Return the root directory used to cache extracted PostgreSQL trees."""
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        root = Path(base) if base else Path.home() / ".cache"
    return root / APP_NAME


def is_root() -> bool:
    """True when running as uid 0 (PostgreSQL refuses to start in that case)."""
    if os.name == "nt":
        return False
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid) and geteuid() == 0


#: Child-process variables that point the dynamic loader at extra libraries.
_LOADER_PATH_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH")
_LOADER_PRELOAD_VARS = ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES")


def sanitize_child_env(env: dict) -> dict:
    """Drop PyInstaller's injected library paths from ``env`` (in place).

    The one-file bootloader puts its temporary extraction directory on
    ``LD_LIBRARY_PATH`` (``DYLD_*`` on macOS). Child processes would then load
    the frozen app's bundled shared libraries instead of the system ones, which
    makes unrelated tools abort - a mismatched ``libreadline`` in particular
    breaks ``/bin/sh`` with "symbol lookup error". The PostgreSQL binaries find
    their own libraries through an ``$ORIGIN/../lib`` rpath, so removing the
    entry is safe. Entries a user set deliberately are preserved.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return env
    root = os.path.abspath(meipass)

    def inside(value: str) -> bool:
        absolute = os.path.abspath(value)
        return absolute == root or absolute.startswith(root + os.sep)

    for name in (*_LOADER_PATH_VARS, *_LOADER_PRELOAD_VARS):
        value = env.get(name)
        if not value:
            continue
        kept = [part for part in value.split(os.pathsep) if part and not inside(part)]
        if kept:
            env[name] = os.pathsep.join(kept)
        else:
            env.pop(name, None)

    return env


def parse_major(version: str) -> int:
    """``"18.6"`` -> ``18``; ``"9.6.24"`` -> ``9``."""
    head = version.split(".")[0]
    digits = "".join(ch for ch in head if ch.isdigit())
    return int(digits) if digits else 0
