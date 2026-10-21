"""End-to-end test against a real PostgreSQL tree.

Skipped unless ``PGPORTABLE_PGSQL_DIR`` points at an extracted ``pgsql``
directory (the one the builder stages, or a cache entry). No download happens,
and the port must be free.

    set PGPORTABLE_PGSQL_DIR=%LOCALAPPDATA%\\pgportable\\18.6-windows-x64\\pgsql
    python -m pytest tests/test_integration.py -q
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PGSQL_DIR = os.environ.get("PGPORTABLE_PGSQL_DIR")
PORT = int(os.environ.get("PGPORTABLE_TEST_PORT", "55432"))

pytestmark = pytest.mark.skipif(
    not PGSQL_DIR, reason="set PGPORTABLE_PGSQL_DIR to run integration tests"
)

SAMPLE_SQL = """
CREATE TABLE items (id serial PRIMARY KEY, name text NOT NULL, qty int NOT NULL DEFAULT 0);
INSERT INTO items (name, qty) VALUES ('alpha', 3), ('beta', 5), ('gamma', 7);
"""


def _pgportable(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "pgportable", "run", "--pgsql-dir", str(PGSQL_DIR), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_plain_in_custom_out_and_back(tmp_path: Path) -> None:
    dump = tmp_path / "sample.sql"
    dump.write_text(SAMPLE_SQL, encoding="utf-8")
    verify = tmp_path / "verify.sql"
    verify.write_text("SELECT count(*) FROM items;\n", encoding="utf-8")

    first = _pgportable(
        tmp_path,
        "--load",
        str(dump),
        "--exec",
        str(verify),
        "--port",
        str(PORT),
        "--dump-out",
        str(tmp_path / "out.dump"),
        "--dump-format",
        "custom",
        "--force-dump",
    )
    assert first.returncode == 0, first.stderr
    assert "3" in first.stdout

    produced = tmp_path / "out.dump"
    assert produced.is_file()
    assert produced.read_bytes()[:5] == b"PGDMP"

    second = _pgportable(
        tmp_path,
        "--load",
        str(produced),
        "--exec",
        str(verify),
        "--port",
        str(PORT),
        "--no-dump-out",
    )
    assert second.returncode == 0, second.stderr
    assert "3" in second.stdout


def test_gzip_plain_dump(tmp_path: Path) -> None:
    import gzip

    plain = tmp_path / "sample.sql"
    plain.write_text(SAMPLE_SQL, encoding="utf-8")
    compressed = tmp_path / "sample.sql.gz"
    with gzip.open(compressed, "wb") as handle:
        handle.write(plain.read_bytes())

    result = _pgportable(
        tmp_path,
        "--load",
        str(compressed),
        "--exec",
        "SELECT count(*) FROM items",
        "--port",
        str(PORT),
        "--no-dump-out",
    )
    assert result.returncode == 0, result.stderr
    assert "3" in result.stdout


def test_scope_all_directory_dump(tmp_path: Path) -> None:
    dump = tmp_path / "sample.sql"
    dump.write_text(SAMPLE_SQL, encoding="utf-8")

    target = tmp_path / "alldump"
    result = _pgportable(
        tmp_path,
        "--load",
        str(dump),
        "--exec",
        "SELECT 1",
        "--port",
        str(PORT),
        "--dump-out",
        str(target),
        "--dump-scope",
        "all",
        "--dump-format",
        "directory",
        "--force-dump",
    )
    assert result.returncode == 0, result.stderr
    assert (target / "globals.sql").is_file()
    assert (target / "postgres" / "toc.dat").is_file()
