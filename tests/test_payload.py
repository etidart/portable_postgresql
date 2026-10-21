from __future__ import annotations

import tarfile
from pathlib import Path

from pgportable import payload as payload_mod
from pgportable.ui import Log


def _log() -> Log:
    return Log("error")


def _make_tree(root: Path) -> Path:
    pgsql = root / "pgsql"
    (pgsql / "bin").mkdir(parents=True)
    (pgsql / "bin" / "postgres").write_text("#!/bin/sh\n")
    (pgsql / "share").mkdir()
    (pgsql / "share" / "postgresql.conf.sample").write_text("# sample\n")
    return pgsql


def _make_archive(root: Path, pgsql: Path) -> Path:
    archive = root / "payload.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(pgsql, arcname="pgsql")
    return archive


def test_append_and_read_footer_roundtrip(tmp_path: Path) -> None:
    pgsql = _make_tree(tmp_path)
    archive = _make_archive(tmp_path, pgsql)
    launcher = tmp_path / "pgportable.exe"
    launcher.write_bytes(b"MZ" + b"\x00" * 1000)
    launcher_size = launcher.stat().st_size

    info = payload_mod.append_payload(launcher, archive, "18.6", "windows-x64", "gz")

    assert info.offset == launcher_size
    assert info.length == archive.stat().st_size
    assert info.version == "18.6"
    assert info.platform == "windows-x64"
    assert info.compression == "gz"

    reread = payload_mod.read_footer(launcher)
    assert reread is not None
    assert reread.offset == info.offset
    assert reread.length == info.length
    assert reread.version == "18.6"
    assert reread.platform == "windows-x64"


def test_read_footer_returns_none_without_payload(tmp_path: Path) -> None:
    plain = tmp_path / "plain.bin"
    plain.write_bytes(b"not a payload" * 100)
    assert payload_mod.read_footer(plain) is None
    assert payload_mod.read_footer(tmp_path / "missing.bin") is None


def test_extract_payload_restores_tree(tmp_path: Path) -> None:
    pgsql = _make_tree(tmp_path / "build")
    archive = _make_archive(tmp_path / "build", pgsql)
    launcher = tmp_path / "pgportable"
    launcher.write_bytes(b"binary")
    payload_mod.append_payload(launcher, archive, "18.6", "linux-amd64", "gz")

    info = payload_mod.read_footer(launcher)
    assert info is not None

    dest = tmp_path / "cache-entry"
    extracted = payload_mod.extract_payload(info, dest, _log())

    assert extracted == dest / "pgsql"
    assert (extracted / "bin" / "postgres").is_file()
    assert (extracted / "share" / "postgresql.conf.sample").read_text() == "# sample\n"


def test_locate_payload_via_environment(tmp_path: Path, monkeypatch) -> None:
    pgsql = _make_tree(tmp_path / "build")
    archive = _make_archive(tmp_path / "build", pgsql)
    sidecar = tmp_path / "pgportable.pgpayload"
    sidecar.write_bytes(b"")
    payload_mod.append_payload(sidecar, archive, "17.2", "linux-amd64", "gz")

    monkeypatch.setenv(payload_mod.ENV_PAYLOAD, str(sidecar))
    info = payload_mod.locate_payload(_log())
    assert info is not None
    assert info.version == "17.2"
