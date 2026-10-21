"""Downloading and archive extraction helpers (stdlib only)."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from pgportable.errors import SourceError
from pgportable.ui import Log

USER_AGENT = "pgportable/0.1 (+https://github.com/)"

_CHUNK = 1024 * 256


def download(
    url: str,
    dest: Path,
    log: Log,
    expected_sha256: str | None = None,
    retries: int = 3,
    timeout: int = 60,
) -> Path:
    """Stream ``url`` to ``dest`` with progress, retries and optional checksum."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        tmp = dest.with_name(dest.name + ".part")
        try:
            log.info(f"Downloading {url}")
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            hasher = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=timeout) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp, "wb") as handle:
                    while True:
                        chunk = response.read(_CHUNK)
                        if not chunk:
                            break
                        handle.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        log.progress(done, total, "    ")
                log.done_progress()

            if expected_sha256:
                actual = hasher.hexdigest().lower()
                if actual != expected_sha256.lower():
                    raise SourceError(
                        f"checksum mismatch for {url}\n  expected {expected_sha256}\n  actual   {actual}"
                    )
            tmp.replace(dest)
            return dest
        except Exception as exc:  # noqa: BLE001 - retried below
            log.done_progress()
            last_error = exc
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            if attempt < retries:
                log.warn(f"attempt {attempt}/{retries} failed ({exc}); retrying")
    raise SourceError(f"failed to download {url}: {last_error}")


def extract_zip(archive: Path, dest: Path) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    return dest


def extract_tar(archive: Path, dest: Path) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tf:
        _extractall(tf, dest)
    return dest


def _extractall(tf: tarfile.TarFile, dest: Path) -> None:
    try:
        tf.extractall(dest, filter="data")  # Python >= 3.12
    except TypeError:
        tf.extractall(dest)


def _has_postgres(bin_dir: Path) -> bool:
    return (bin_dir / "postgres").is_file() or (bin_dir / "postgres.exe").is_file()


def materialize_pgsql(root: Path, workdir: Path) -> Path:
    """Return a directory literally named ``pgsql`` holding ``bin/``/``lib/``/``share/``.

    Different vendors ship the tree differently: EnterpriseDB zips already have a
    top-level ``pgsql/`` folder, while theseus tarballs unpack a
    ``postgresql-<version>-<target>/`` directory containing ``bin/``.
    """
    root = Path(root)
    workdir = Path(workdir)

    candidate = root / "pgsql"
    if _has_postgres(candidate / "bin"):
        return candidate

    if _has_postgres(root / "bin"):
        return _move_to_pgsql(root, workdir)

    for bin_dir in sorted(root.rglob("bin")):
        if bin_dir.is_dir() and _has_postgres(bin_dir):
            parent = bin_dir.parent
            if parent.name == "pgsql":
                return parent
            return _move_to_pgsql(parent, workdir)

    raise SourceError(f"could not locate a PostgreSQL 'bin' directory under {root}")


def _move_to_pgsql(source: Path, workdir: Path) -> Path:
    target = workdir / "pgsql"
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(source), str(target))
    return target
