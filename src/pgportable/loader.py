"""Detect a dump's format and load it into a running instance."""

from __future__ import annotations

import bz2
import gzip
import lzma
import re
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pgportable.config import DEFAULT_DB
from pgportable.errors import InstanceError, RestoreError
from pgportable.instance import Instance
from pgportable.ui import Log

CUSTOM_MAGIC = b"PGDMP"
TAR_MAGIC_OFFSET = 257
TAR_MAGIC = b"ustar"

COMPRESSED_SUFFIXES = {
    ".gz": "gzip",
    ".bz2": "bzip2",
    ".xz": "xz",
    ".lzma": "lzma",
    ".zst": "zstd",
    ".zstd": "zstd",
}

_CREATES_DB = re.compile(r"^\s*(CREATE\s+DATABASE|\\connect)\b", re.IGNORECASE | re.MULTILINE)


class DumpKind(str, Enum):
    PLAIN = "plain"
    CUSTOM = "custom"
    TAR = "tar"
    DIRECTORY = "directory"


@dataclass
class Detected:
    kind: DumpKind
    path: Path
    temp: Path | None = None

    def cleanup(self) -> None:
        if self.temp is not None:
            self.temp.unlink(missing_ok=True)
            self.temp = None


def _detect_file(path: Path) -> Detected:
    with open(path, "rb") as handle:
        head = handle.read(512)
    if head[:5] == CUSTOM_MAGIC:
        return Detected(DumpKind.CUSTOM, path)
    if len(head) >= TAR_MAGIC_OFFSET + 5 and head[TAR_MAGIC_OFFSET : TAR_MAGIC_OFFSET + 5] == TAR_MAGIC:
        return Detected(DumpKind.TAR, path)
    return Detected(DumpKind.PLAIN, path)


def _decompress(path: Path) -> Path:
    suffix = path.suffix.lower()
    kind = COMPRESSED_SUFFIXES[suffix]
    handle = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".dump")
    temp = Path(handle.name)
    handle.close()

    try:
        if kind == "gzip":
            with gzip.open(path, "rb") as src, open(temp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif kind == "bzip2":
            with bz2.open(path, "rb") as src, open(temp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif kind == "xz":
            with lzma.open(path, "rb") as src, open(temp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif kind == "lzma":
            with lzma.open(path, "rb") as src, open(temp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        elif kind == "zstd":
            try:
                import zstandard  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RestoreError(
                    "zstd-compressed dumps need the optional 'zstandard' package "
                    "(pip install zstandard), or decompress the file first."
                ) from exc
            with open(path, "rb") as src, open(temp, "wb") as dst:
                dctx = zstandard.ZstdDecompressor()
                dctx.copy_stream(src, dst)
        else:  # pragma: no cover - guarded by suffix map
            raise RestoreError(f"unsupported compression: {suffix}")
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return temp


def detect(dump_path: Path) -> Detected:
    path = Path(dump_path).expanduser()
    if not path.exists():
        raise RestoreError(f"dump file not found: {path}")

    if path.is_dir():
        if (path / "toc.dat").is_file():
            return Detected(DumpKind.DIRECTORY, path)
        raise RestoreError(
            f"{path} is a directory but has no toc.dat - it is not a pg_dump directory archive"
        )

    suffix = path.suffix.lower()
    if suffix in COMPRESSED_SUFFIXES:
        decompressed = _decompress(path)
        try:
            detected = _detect_file(decompressed)
        except Exception:
            decompressed.unlink(missing_ok=True)
            raise
        detected.temp = decompressed
        return detected

    return _detect_file(path)


def _plain_creates_database(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            chunk = handle.read(65536)
    except OSError:
        return False
    return bool(_CREATES_DB.search(chunk))


def load(
    instance: Instance,
    dump_path: Path,
    db: str | None = None,
    log: Log | None = None,
    ignore_errors: bool = False,
    clean: bool = False,
) -> str:
    """Restore ``dump_path``; returns the database the dump was applied to."""
    detected = detect(dump_path)
    log = log or instance.log
    try:
        log.step(f"Loading {Path(dump_path).name} ({detected.kind.value} format)")

        if detected.kind is DumpKind.PLAIN:
            creates = _plain_creates_database(detected.path)
            target = DEFAULT_DB if creates else (db or DEFAULT_DB)
            if not creates:
                instance.ensure_database(target)
            else:
                log.info("Dump contains CREATE DATABASE / \\connect; applying as-is")

            extra = ["-f", str(detected.path)]
            if ignore_errors:
                # psql honours the last -v, so this overrides ON_ERROR_STOP=1.
                extra = ["-v", "ON_ERROR_STOP=0", *extra]
            _guarded_run(instance, instance.psql_cmd(target, extra))
            return target

        target = db or DEFAULT_DB
        instance.ensure_database(target)

        format_flag = []
        if detected.kind is DumpKind.TAR:
            format_flag = ["-F", "t"]
        elif detected.kind is DumpKind.DIRECTORY:
            format_flag = ["-F", "d"]

        command = [
            instance._bin("pg_restore"),
            "-h",
            instance.host,
            "-p",
            str(instance.port),
            "-U",
            instance.user,
            "-d",
            target,
            "--no-owner",
            "--no-privileges",
            *format_flag,
        ]
        if clean:
            command += ["--clean", "--if-exists"]
        if not ignore_errors:
            command.append("--exit-on-error")
        command.append(str(detected.path))

        _guarded_run(instance, command)
        return target
    finally:
        detected.cleanup()


def _guarded_run(instance: Instance, command: list) -> None:
    """Run a client command, reporting failures as restore errors."""
    try:
        instance._run(command, capture=True)
    except InstanceError as exc:
        raise RestoreError(str(exc)) from exc
