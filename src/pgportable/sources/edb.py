"""EnterpriseDB binary archives (Windows).

EDB publishes the exact files produced by its installers as a ``.zip`` that
already contains a top-level ``pgsql/`` directory. Modern releases expose the
archive through a ``getfile.jsp?fileid=`` redirect, but the deterministic
``get.enterprisedb.com/postgresql/postgresql-<ver>-1-windows-x64-binaries.zip``
pattern keeps working and is what we use.
"""

from __future__ import annotations

import re
import urllib.request
from html import unescape
from pathlib import Path

from pgportable.bundle import USER_AGENT, download, extract_zip, materialize_pgsql
from pgportable.sources.base import Provider, version_key
from pgportable.ui import Log

EDB_ARCHIVE = (
    "https://get.enterprisedb.com/postgresql/"
    "postgresql-{version}-1-windows-x64-binaries.zip"
)
EDB_PAGE = "https://www.enterprisedb.com/download-postgresql-binaries"

#: Used when the download page cannot be reached/parsed.
FALLBACK_LATEST = "18.6"


class EdbProvider(Provider):
    name = "edb"
    platforms = ("windows-x64",)

    def latest_version(self, platform: str, log: Log) -> str:
        try:
            request = urllib.request.Request(EDB_PAGE, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                html = response.read().decode("utf-8", "replace")
            # Version strings are split across markup, so flatten tags/entities first.
            text = unescape(re.sub(r"<[^>]+>", " ", html)).replace("\\", "")
            found = {m.group(1) for m in re.finditer(r"Version\s*(\d+\.\d+)", text)}
            if found:
                best = max(found, key=version_key)
                log.debug(f"EDB versions: {', '.join(sorted(found, key=version_key))}")
                return best
            log.warn("EDB page parsed but contained no versions")
        except Exception as exc:  # noqa: BLE001
            log.warn(f"could not query the EDB download page: {exc}")
        log.warn(f"falling back to a known-good EDB version: {FALLBACK_LATEST}")
        return FALLBACK_LATEST

    def archive_url(self, version: str, platform: str) -> str:
        return EDB_ARCHIVE.format(version=version)

    def fetch(self, version: str, platform: str, workdir: Path, log: Log) -> Path:
        workdir = Path(workdir)
        archive = workdir / f"postgresql-edb-{version}.zip"
        if not archive.exists():
            download(self.archive_url(version, platform), archive, log)
        else:
            log.debug(f"reusing cached archive {archive}")

        extract_root = workdir / "edb-extract"
        if not (extract_root / "pgsql" / "bin").is_dir():
            log.info(f"Extracting {archive.name}")
            extract_zip(archive, extract_root)

        pgsql = materialize_pgsql(extract_root, workdir)
        log.info(f"PostgreSQL {version} staged at {pgsql}")
        return pgsql
