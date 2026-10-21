"""zonky embedded-postgres-binaries (Linux/macOS, via Maven Central).

EDB does not publish modern Linux archives, so Linux builds use the
``io.zonky.test.postgres`` artifacts. Each release is a jar (really a zip)
containing a ``postgres-<os>-<arch>.txz`` tarball whose contents are the usual
``bin/``, ``lib/`` and ``share/`` directories, including ``pg_dump`` and
``pg_restore``.
"""

from __future__ import annotations

import re
import urllib.request
import zipfile
from pathlib import Path

from pgportable.bundle import USER_AGENT, download, extract_tar, materialize_pgsql
from pgportable.errors import SourceError
from pgportable.sources.base import Provider, version_key
from pgportable.ui import Log

ZONKY_BASE = "https://repo1.maven.org/maven2/io/zonky/test/postgres"

ARTIFACTS = {
    "linux-amd64": "embedded-postgres-binaries-linux-amd64",
    "linux-arm64": "embedded-postgres-binaries-linux-arm64v8",
    "macos-x64": "embedded-postgres-binaries-darwin-amd64",
    "macos-arm64": "embedded-postgres-binaries-darwin-arm64v8",
}


def _pg_version(zonky_version: str) -> str:
    """Map a zonky version to the PostgreSQL version it contains.

    ``"18.6.0"`` -> ``"18.6"``; ``"9.6.24"`` stays ``"9.6.24"``.
    """
    parts = zonky_version.split("-")[0].split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    if major >= 10 and len(parts) >= 2:
        return f"{major}.{int(parts[1])}"
    return zonky_version


class ZonkyProvider(Provider):
    name = "zonky"
    platforms = tuple(ARTIFACTS)

    def _metadata(self, platform: str, log: Log) -> list[str]:
        artifact = ARTIFACTS[platform]
        url = f"{ZONKY_BASE}/{artifact}/maven-metadata.xml"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            xml = response.read().decode("utf-8", "replace")
        versions = re.findall(r"<version>([^<]+)</version>", xml)
        if not versions:
            raise SourceError(f"no versions listed in {url}")
        log.debug(f"zonky {artifact}: {len(versions)} versions available")
        return versions

    def latest_version(self, platform: str, log: Log) -> str:
        versions = self._metadata(platform, log)
        newest = max(versions, key=version_key)
        return _pg_version(newest)

    def _resolve_full_version(self, version: str, platform: str, log: Log) -> str:
        versions = self._metadata(platform, log)
        matches = [v for v in versions if _pg_version(v) == version]
        if not matches:
            available = sorted({_pg_version(v) for v in versions}, key=version_key)
            raise SourceError(
                f"zonky has no build for PostgreSQL {version} on {platform}. "
                f"Newest available: {available[-1]}"
            )
        return max(matches, key=version_key)

    def archive_url(self, version: str, platform: str) -> str:
        artifact = ARTIFACTS[platform]
        return (
            f"{ZONKY_BASE}/{artifact}/{version}/{artifact}-{version}.jar"
        )

    def fetch(self, version: str, platform: str, workdir: Path, log: Log) -> Path:
        workdir = Path(workdir)
        full = self._resolve_full_version(version, platform, log)
        artifact = ARTIFACTS[platform]

        jar = workdir / f"{artifact}-{full}.jar"
        if not jar.exists():
            download(self.archive_url(full, platform), jar, log)
        else:
            log.debug(f"reusing cached archive {jar}")

        extract_root = workdir / "zonky-extract"
        inner_txz: Path | None = None
        if extract_root.is_dir():
            found = list(extract_root.rglob("*.txz"))
            inner_txz = found[0] if found else None
        if inner_txz is None:
            log.info(f"Extracting {jar.name}")
            extract_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(jar) as zf:
                txz_members = [n for n in zf.namelist() if n.endswith(".txz")]
                if not txz_members:
                    raise SourceError(f"no .txz payload inside {jar}")
                zf.extract(txz_members[0], extract_root)
            inner_txz = extract_root / txz_members[0]

        payload_root = workdir / "zonky-payload"
        if not (payload_root / "bin").is_dir():
            log.info(f"Unpacking {inner_txz.name}")
            extract_tar(inner_txz, payload_root)

        pgsql = materialize_pgsql(payload_root, workdir)
        log.info(f"PostgreSQL {version} staged at {pgsql}")
        return pgsql
