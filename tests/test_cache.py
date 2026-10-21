from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from pgportable import cache as cache_mod
from pgportable.bundle import materialize_pgsql
from pgportable.errors import SourceError


def test_cache_key_is_version_and_platform() -> None:
    assert cache_mod.cache_key("18.6", "linux-amd64") == "18.6-linux-amd64"


def test_cache_entry_dir_uses_override(tmp_path: Path) -> None:
    entry = cache_mod.entry_dir("18.6", "windows-x64", str(tmp_path))
    assert entry == tmp_path / "18.6-windows-x64"


def test_is_ready_requires_marker_and_bin(tmp_path: Path) -> None:
    entry = tmp_path / "18.6-linux-amd64"
    key = "18.6-linux-amd64"
    assert not cache_mod.is_ready(entry, key)

    (entry / "pgsql" / "bin").mkdir(parents=True)
    assert not cache_mod.is_ready(entry, key)

    cache_mod.write_marker(entry, key)
    assert cache_mod.is_ready(entry, key)


def test_is_ready_rejects_version_mismatch(tmp_path: Path) -> None:
    entry = tmp_path / "18.6-linux-amd64"
    (entry / "pgsql" / "bin").mkdir(parents=True)
    cache_mod.write_marker(entry, "17.2-linux-amd64")
    assert not cache_mod.is_ready(entry, "18.6-linux-amd64")


def test_prune_removes_entries(tmp_path: Path) -> None:
    for key in ("18.6-linux-amd64", "17.2-linux-amd64"):
        entry = tmp_path / key
        (entry / "pgsql" / "bin").mkdir(parents=True)
        cache_mod.write_marker(entry, key)

    removed = cache_mod.prune(str(tmp_path))
    assert removed == 2
    assert cache_mod.list_entries(str(tmp_path)) == []


def test_materialize_pgsql_handles_pgsql_prefix(tmp_path: Path) -> None:
    root = tmp_path / "extract"
    (root / "pgsql" / "bin").mkdir(parents=True)
    (root / "pgsql" / "bin" / "postgres").write_text("x")
    assert materialize_pgsql(root, tmp_path / "work") == root / "pgsql"


def test_materialize_pgsql_wraps_bare_tree(tmp_path: Path) -> None:
    root = tmp_path / "extract"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "postgres").write_text("x")
    (root / "share").mkdir()

    pgsql = materialize_pgsql(root, tmp_path / "work")
    assert pgsql.name == "pgsql"
    assert (pgsql / "bin" / "postgres").is_file()
    assert (pgsql / "share").is_dir()


def test_materialize_pgsql_errors_without_bin(tmp_path: Path) -> None:
    root = tmp_path / "extract"
    root.mkdir()
    with pytest.raises(SourceError):
        materialize_pgsql(root, tmp_path / "work")


def test_archive_preserves_tree(tmp_path: Path) -> None:
    pgsql = tmp_path / "pgsql"
    (pgsql / "bin").mkdir(parents=True)
    (pgsql / "bin" / "pg_dump").write_text("x")
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(pgsql, arcname="pgsql")

    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:
            tf.extractall(dest)
    assert (dest / "pgsql" / "bin" / "pg_dump").is_file()
