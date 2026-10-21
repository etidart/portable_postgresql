"""theseus-rs/postgresql-binaries (Linux, macOS, Windows, via GitHub Releases).

Unlike zonky's trimmed builds, these archives ship the complete PostgreSQL
distribution: ``initdb``, ``pg_ctl`` and ``postgres`` alongside ``psql``,
``pg_dump``, ``pg_restore``, ``pg_isready`` and the rest of the client tools.
The shared libraries live in ``lib/`` and are found through an
``$ORIGIN/../lib`` rpath, so the extracted tree stays relocatable.

Release tags carry an extra build-revision component: tag ``17.5.1`` contains
PostgreSQL ``17.5``. Assets are named
``postgresql-<tag>-<rust-target-triple>.tar.gz`` and each one has a matching
``.sha256`` sidecar.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.request
from pathlib import Path

from pgportable.bundle import USER_AGENT, download, extract_tar, materialize_pgsql
from pgportable.errors import SourceError
from pgportable.sources.base import Provider, version_key
from pgportable.ui import Log

GH_API = "https://api.github.com/repos/theseus-rs/postgresql-binaries"
GH_DOWNLOAD = "https://github.com/theseus-rs/postgresql-binaries/releases/download"

#: pgportable platform id -> Rust target triple used in asset names.
TARGETS = {
    "linux-amd64": "x86_64-unknown-linux-gnu",
    "linux-arm64": "aarch64-unknown-linux-gnu",
    "macos-x64": "x86_64-apple-darwin",
    "macos-arm64": "aarch64-apple-darwin",
    "windows-x64": "x86_64-pc-windows-msvc",
    "windows-arm64": "aarch64-pc-windows-msvc",
}

_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def pg_version(tag: str) -> str:
    """Map a theseus release tag to the PostgreSQL version it ships.

    ``"17.5.1"`` -> ``"17.5"`` (the final component is theseus' build revision);
    unrecognized tags are returned unchanged.
    """
    match = _TAG_RE.match(tag)
    if not match:
        return tag
    return f"{int(match.group(1))}.{int(match.group(2))}"


class TheseusProvider(Provider):
    name = "theseus"
    platforms = tuple(TARGETS)

    def __init__(self) -> None:
        self._tags_cache: list[str] | None = None

    # -- GitHub metadata ---------------------------------------------------
    @staticmethod
    def _headers() -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        # An (optional) token lifts GitHub's anonymous API rate limit, which
        # matters on shared CI runners.
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _tags(self, log: Log) -> list[str]:
        if self._tags_cache is not None:
            return self._tags_cache

        tags: list[str] = []
        page = 1
        while True:
            url = f"{GH_API}/releases?per_page=100&page={page}"
            request = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.load(response)
            except Exception as exc:  # noqa: BLE001 - surfaced as SourceError
                raise SourceError(f"could not list theseus releases: {exc}") from exc
            if not isinstance(data, list) or not data:
                break
            tags.extend(item["tag_name"] for item in data if item.get("tag_name"))
            if len(data) < 100:
                break
            page += 1

        if not tags:
            raise SourceError("theseus release list is empty")
        self._tags_cache = tags
        log.debug(f"theseus: {len(tags)} releases available")
        return tags

    def latest_version(self, platform: str, log: Log) -> str:
        tags = self._tags(log)
        return pg_version(max(tags, key=version_key))

    def _resolve_tag(self, version: str, platform: str, log: Log) -> str:
        tags = self._tags(log)
        if version in tags:
            return version
        matches = [tag for tag in tags if pg_version(tag) == version]
        if not matches:
            available = sorted({pg_version(tag) for tag in tags}, key=version_key)
            raise SourceError(
                f"theseus has no build for PostgreSQL {version} on {platform}. "
                f"Newest available: {available[-1]}"
            )
        return max(matches, key=version_key)

    # -- download ----------------------------------------------------------
    def archive_url(self, version: str, platform: str) -> str:
        target = TARGETS[platform]
        return f"{GH_DOWNLOAD}/{version}/postgresql-{version}-{target}.tar.gz"

    def _expected_sha256(self, url: str, log: Log) -> str | None:
        request = urllib.request.Request(url + ".sha256", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - checksum is best-effort
            log.debug(f"could not fetch checksum for {url}: {exc}")
            return None
        match = _SHA256_RE.search(text)
        return match.group(0).lower() if match else None

    def fetch(self, version: str, platform: str, workdir: Path, log: Log) -> Path:
        workdir = Path(workdir)
        full = self._resolve_tag(version, platform, log)
        target = TARGETS[platform]
        archive = workdir / f"postgresql-{full}-{target}.tar.gz"

        if not archive.exists():
            url = self.archive_url(full, platform)
            download(url, archive, log, expected_sha256=self._expected_sha256(url, log))
        else:
            log.debug(f"reusing cached archive {archive}")

        pgsql = workdir / "pgsql"
        stamp = workdir / "theseus.stamp"
        if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == full and (pgsql / "bin").is_dir():
            log.debug(f"reusing staged tree {pgsql}")
            return pgsql

        extract_root = workdir / "theseus-extract"
        log.info(f"Extracting {archive.name}")
        shutil.rmtree(extract_root, ignore_errors=True)
        extract_tar(archive, extract_root)
        pgsql = materialize_pgsql(extract_root, workdir)
        shutil.rmtree(extract_root, ignore_errors=True)
        stamp.write_text(full, encoding="utf-8")
        log.info(f"PostgreSQL {version} staged at {pgsql}")
        return pgsql
