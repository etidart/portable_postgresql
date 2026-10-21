from __future__ import annotations

import gzip
import tarfile
from pathlib import Path

import pytest

from pgportable import loader
from pgportable.loader import DumpKind


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_detects_custom_format(tmp_path: Path) -> None:
    dump = _write(tmp_path / "db.dump", b"PGDMP\x01\x0e\x00" + b"\x00" * 100)
    detected = loader.detect(dump)
    assert detected.kind is DumpKind.CUSTOM
    assert detected.temp is None


def test_detects_tar_format(tmp_path: Path) -> None:
    dump = _write(tmp_path / "db.tar", (b"\x00" * 257) + b"ustar\x00\x30\x30")
    assert loader.detect(dump).kind is DumpKind.TAR


def test_detects_plain_format(tmp_path: Path) -> None:
    dump = _write(tmp_path / "db.sql", b"SET statement_timeout = 0;\nCREATE TABLE t (id int);\n")
    assert loader.detect(dump).kind is DumpKind.PLAIN


def test_detects_directory_format(tmp_path: Path) -> None:
    directory = tmp_path / "db.dir"
    directory.mkdir()
    (directory / "toc.dat").write_bytes(b"PGDMP")
    assert loader.detect(directory).kind is DumpKind.DIRECTORY


def test_directory_without_toc_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "empty"
    directory.mkdir()
    with pytest.raises(loader.RestoreError):
        loader.detect(directory)


def test_gzip_plain_dump_is_decompressed(tmp_path: Path) -> None:
    plain = b"CREATE TABLE t (id int);\n"
    dump = tmp_path / "db.sql.gz"
    with gzip.open(dump, "wb") as handle:
        handle.write(plain)

    detected = loader.detect(dump)
    assert detected.kind is DumpKind.PLAIN
    assert detected.temp is not None
    assert detected.temp.read_bytes() == plain
    detected.cleanup()
    assert not detected.temp


def test_gzip_custom_dump_keeps_format(tmp_path: Path) -> None:
    dump = tmp_path / "db.dump.gz"
    with gzip.open(dump, "wb") as handle:
        handle.write(b"PGDMP" + b"\x00" * 64)
    detected = loader.detect(dump)
    assert detected.kind is DumpKind.CUSTOM
    detected.cleanup()


def test_missing_dump_raises(tmp_path: Path) -> None:
    with pytest.raises(loader.RestoreError):
        loader.detect(tmp_path / "nope.sql")


def test_plain_creates_database_detection(tmp_path: Path) -> None:
    with_db = tmp_path / "with.sql"
    with_db.write_text("-- header\nCREATE DATABASE app;\n\\connect app\n")
    assert loader._plain_creates_database(with_db)

    without_db = tmp_path / "without.sql"
    without_db.write_text("CREATE TABLE t (id int);\n")
    assert not loader._plain_creates_database(without_db)


def test_tarfile_roundtrip_matches_detection(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "file.txt").write_text("hello")
    archive = tmp_path / "a.tar"
    with tarfile.open(archive, "w") as tf:
        tf.add(payload / "file.txt", arcname="payload/file.txt")
    assert loader.detect(archive).kind is DumpKind.TAR
