"""Version-keyed cache of extracted PostgreSQL trees.

The single-file artifact embeds one specific PostgreSQL build. On first run the
payload is unpacked into ``<cache>/<pg-version>-<platform>/`` and a marker file
records which key is present. Subsequent runs whose bundled key matches reuse
the directory untouched; a mismatch triggers a fresh extraction.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pgportable.config import MARKER_FILE, cache_root
from pgportable.ui import Log


def cache_key(pg_version: str, platform: str) -> str:
    return f"{pg_version}-{platform}"


def entry_dir(pg_version: str, platform: str, override: str | None = None) -> Path:
    return cache_root(override) / cache_key(pg_version, platform)


def _marker(entry: Path) -> Path:
    return entry / MARKER_FILE


def read_marker(entry: Path) -> dict | None:
    marker = _marker(entry)
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_marker(entry: Path, key: str) -> None:
    payload = {
        "key": key,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _marker(entry).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_ready(entry: Path, key: str) -> bool:
    """True when ``entry`` holds a complete tree for exactly ``key``."""
    if not (entry / "pgsql" / "bin").is_dir():
        return False
    marker = read_marker(entry)
    return bool(marker and marker.get("key") == key)


def list_entries(override: str | None = None) -> list[Path]:
    root = cache_root(override)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def prune(override: str | None = None, log: Log | None = None) -> int:
    root = cache_root(override)
    entries = list_entries(override)
    for entry in entries:
        if log:
            log.info(f"Removing {entry}")
        shutil.rmtree(entry, ignore_errors=True)
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()
    return len(entries)
