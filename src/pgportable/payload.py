"""Embedding the ``pgsql`` payload inside / next to the launcher.

Layout of a payload-bearing file::

    [ launcher: .exe / ELF bytes ]
    [ compressed pgsql tree: tar.gz or tar.xz ]
    [ footer: MAGIC | offset | length | compression | platform | version ]

The footer is fixed width (72 bytes) and sits at the very end of the file, so
reading it is a single seek. PyInstaller's own archive lives inside the launcher
region and is unaffected by the appended bytes.
"""

from __future__ import annotations

import io
import json
import os
import stat
import struct
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

from pgportable.config import is_frozen
from pgportable.errors import PayloadError
from pgportable.ui import Log

MAGIC = b"PGPORTBL"
_STRUCT = struct.Struct("<8sQQ8s16s24s")
FOOTER_SIZE = _STRUCT.size  # 72 bytes

SIDECAR_SUFFIX = ".pgpayload"
ENV_PAYLOAD = "PGPORTABLE_PAYLOAD"


@dataclass(frozen=True)
class PayloadInfo:
    path: Path
    offset: int
    length: int
    compression: str
    platform: str
    version: str

    @property
    def key_version(self) -> str:
        return self.version


def _pack(offset: int, length: int, compression: str, platform: str, version: str) -> bytes:
    return _STRUCT.pack(
        MAGIC,
        offset,
        length,
        compression.encode("ascii")[:8],
        platform.encode("ascii")[:16],
        version.encode("ascii")[:24],
    )


def append_payload(
    target: Path,
    archive: Path,
    version: str,
    platform: str,
    compression: str,
) -> PayloadInfo:
    """Append ``archive`` plus a footer to ``target`` (an exe or a payload file)."""
    target = Path(target)
    archive = Path(archive)
    offset = target.stat().st_size
    length = archive.stat().st_size

    with open(target, "ab") as out, open(archive, "rb") as src:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
        out.write(_pack(offset, length, compression, platform, version))

    return PayloadInfo(target, offset, length, compression, platform, version)


def read_footer(path: Path) -> PayloadInfo | None:
    """Return footer info for ``path`` if it carries a payload, else ``None``."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size < FOOTER_SIZE:
        return None

    with open(path, "rb") as handle:
        handle.seek(size - FOOTER_SIZE)
        raw = handle.read(FOOTER_SIZE)

    magic, offset, length, compression, platform, version = _STRUCT.unpack(raw)
    if magic != MAGIC:
        return None

    info = PayloadInfo(
        path=path,
        offset=offset,
        length=length,
        compression=compression.decode("ascii").strip("\x00"),
        platform=platform.decode("ascii").strip("\x00"),
        version=version.decode("ascii").strip("\x00"),
    )
    if info.offset + info.length > size:
        return None
    return info


def locate_payload(log: Log | None = None) -> PayloadInfo | None:
    """Find the payload for the current process.

    Order: appended to the running executable, a sibling ``*.pgpayload`` file,
    then the ``PGPORTABLE_PAYLOAD`` environment variable.
    """
    candidates: list[Path] = []

    if is_frozen():
        candidates.append(Path(sys.executable))
        candidates.append(Path(sys.executable).with_name(Path(sys.executable).name + SIDECAR_SUFFIX))
    else:
        candidates.append(Path(sys.argv[0]).resolve())
        candidates.append(Path(sys.argv[0]).resolve().with_name(Path(sys.argv[0]).name + SIDECAR_SUFFIX))
        candidates.append(Path.cwd() / "dist")

    env = os.environ.get(ENV_PAYLOAD)
    if env:
        candidates.insert(0, Path(env).expanduser())

    for candidate in candidates:
        if candidate.is_dir():
            for child in sorted(candidate.glob(f"*{SIDECAR_SUFFIX}")):
                info = read_footer(child)
                if info:
                    if log:
                        log.debug(f"payload found at {child} (pg {info.version})")
                    return info
            continue
        if not candidate.is_file():
            continue
        info = read_footer(candidate)
        if info:
            if log:
                log.debug(f"payload found at {candidate} (pg {info.version})")
            return info
    return None


class _SlicedReader(io.RawIOBase):
    """Read/seek within a byte range of the wrapped file object."""

    def __init__(self, handle, start: int, length: int) -> None:
        self._handle = handle
        self._start = start
        self._length = length
        self._pos = 0

    def readable(self) -> bool:  # pragma: no cover - trivial
        return True

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            target = self._length + offset
        else:
            raise ValueError(f"invalid whence: {whence}")
        self._pos = max(0, min(target, self._length))
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self._length:
            return b""
        if size is None or size < 0:
            size = self._length - self._pos
        size = min(size, self._length - self._pos)
        self._handle.seek(self._start + self._pos)
        data = self._handle.read(size)
        self._pos += len(data)
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _extract_tar(stream, dest: Path) -> None:
    with tarfile.open(fileobj=stream, mode="r:*") as tf:
        try:
            tf.extractall(dest, filter="data")  # Python >= 3.12
        except TypeError:
            tf.extractall(dest)


def extract_payload(info: PayloadInfo, dest: Path, log: Log) -> Path:
    """Unpack the payload into ``dest`` and return ``dest/pgsql``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    log.info(f"Extracting PostgreSQL {info.version} to {dest}")

    with open(info.path, "rb") as handle:
        _extract_tar(_SlicedReader(handle, info.offset, info.length), dest)

    pgsql = dest / "pgsql"
    if not (pgsql / "bin").is_dir():
        raise PayloadError(f"payload did not contain a pgsql/bin directory ({dest})")
    make_executable(pgsql)
    return pgsql


def make_executable(pgsql: Path) -> None:
    """Ensure binaries and shared libraries are executable (no-op on Windows)."""
    if os.name == "nt":
        return
    for directory in ("bin", "lib"):
        target = pgsql / directory
        if not target.is_dir():
            continue
        for item in target.rglob("*"):
            try:
                current = item.stat().st_mode
                item.chmod(current | stat.S_IXUSR)
                if item.is_dir():
                    item.chmod(item.stat().st_mode | stat.S_IXUSR)
            except OSError:
                pass


def sidecar_path(exe: Path) -> Path:
    exe = Path(exe)
    return exe.with_name(exe.name + SIDECAR_SUFFIX)


def write_meta(path: Path, info: PayloadInfo) -> None:
    """Human-readable sidecar metadata (diagnostics only; runtime uses the footer)."""
    meta = {
        "version": info.version,
        "platform": info.platform,
        "compression": info.compression,
        "offset": info.offset,
        "length": info.length,
    }
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
