from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from pgportable import cli, dumper


def test_default_command_injection() -> None:
    assert cli._inject_default_command([]) == ["run", "--help"]
    assert cli._inject_default_command(["build"]) == ["build"]
    assert cli._inject_default_command(["--help"]) == ["--help"]
    assert cli._inject_default_command(["--load", "x.sql"]) == ["run", "--load", "x.sql"]
    assert cli._inject_default_command(["cache", "list"]) == ["cache", "list"]


def test_parser_run_defaults() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--load", "a.sql", "--load", "b.dump"])
    assert args.load == ["a.sql", "b.dump"]
    assert args.port == 5432
    assert args.dump_format == "plain"
    assert args.dump_scope == "db"
    assert args.no_dump_out is False
    assert args.user == "postgres"


def test_parser_build_defaults() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["build"])
    assert args.pg_version == "latest"
    assert args.source == "auto"
    assert args.compression == "gz"
    assert args.onedir is False


def test_parser_rejects_unknown_dump_format() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--dump-format", "nope"])


def test_default_output_naming() -> None:
    when = datetime(2026, 10, 21, 15, 30, 0)
    assert dumper.default_output("plain", when).name == "pgdump-20261021-153000.sql"
    assert dumper.default_output("custom", when).name == "pgdump-20261021-153000.dump"
    assert dumper.default_output("tar", when).name == "pgdump-20261021-153000.tar"
    assert dumper.default_output("directory", when).name == "pgdump-20261021-153000"


def test_prepare_rejects_existing_without_force(tmp_path: Path) -> None:
    existing = tmp_path / "dump.sql"
    existing.write_text("data")
    with pytest.raises(dumper.DumpError):
        dumper._prepare(existing, force=False, is_dir=False)
    dumper._prepare(existing, force=True, is_dir=False)


def test_prepare_allows_empty_dir(tmp_path: Path) -> None:
    target = tmp_path / "outdir"
    target.mkdir()
    dumper._prepare(target, force=False, is_dir=True)
