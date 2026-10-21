"""Build the portable single-file artifact: fetch -> pack -> PyInstaller -> embed."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from pgportable import payload as payload_mod
from pgportable.config import current_platform, exe_suffix
from pgportable.errors import BundlingError
from pgportable.sources import select_provider
from pgportable.ui import Log, human_bytes

EXCLUDED_MODULES = (
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "sqlite3",
    "lib2to3",
)


def make_archive(pgsql_dir: Path, archive: Path, compression: str, log: Log) -> Path:
    """Pack ``pgsql_dir`` as ``pgsql/`` inside a tar archive."""
    if compression == "xz":
        mode, kwargs = "w:xz", {"preset": 6}
    else:
        mode, kwargs = "w:gz", {"compresslevel": 6}

    entries = sorted(p for p in pgsql_dir.rglob("*"))
    total = len(entries) + 1
    log.info(f"Packing {total} entries with {compression} compression")

    with tarfile.open(archive, mode, **kwargs) as tf:
        tf.add(pgsql_dir, arcname="pgsql", recursive=False)
        log.progress(1, total, "    ")
        for index, item in enumerate(entries, start=2):
            arcname = "pgsql/" + item.relative_to(pgsql_dir).as_posix()
            tf.add(item, arcname=arcname, recursive=False)
            log.progress(index, total, "    ")
    log.done_progress()
    return archive


def _run_pyinstaller(work_dir: Path, name: str, out_dir: Path, onedir: bool, log: Log) -> Path:
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise BundlingError(
            "PyInstaller is required to build the artifact: pip install pyinstaller"
        ) from exc

    launcher = work_dir / "launcher.py"
    launcher.write_text(
        "from pgportable.cli import main\nraise SystemExit(main())\n", encoding="utf-8"
    )

    package_parent = Path(__file__).resolve().parents[1]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(out_dir),
        "--workpath",
        str(work_dir / "pyi-build"),
        "--specpath",
        str(work_dir / "pyi-spec"),
        "--paths",
        str(package_parent),
        "--onefile" if not onedir else "--onedir",
    ]
    for module in EXCLUDED_MODULES:
        cmd += ["--exclude-module", module]
    cmd.append(str(launcher))

    log.step("Running PyInstaller")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail_out = (result.stdout or "")[-4000:]
        tail_err = (result.stderr or "")[-4000:]
        raise BundlingError(f"PyInstaller failed:\n{tail_out}\n{tail_err}")
    log.debug((result.stdout or "")[-2000:])

    if onedir:
        exe = out_dir / name / f"{name}{exe_suffix()}"
    else:
        exe = out_dir / f"{name}{exe_suffix()}"
    if not exe.is_file():
        raise BundlingError(f"PyInstaller reported success but {exe} is missing")
    return exe


def build(
    pg_version: str,
    source: str,
    out_dir: Path,
    name: str,
    compression: str,
    work_dir: Path | None,
    keep_work: bool,
    onedir: bool,
    platform: str | None,
    log: Log,
) -> Path:
    platform = platform or current_platform()
    provider = select_provider(source, platform, log)

    version = pg_version
    if not version or str(version).lower() == "latest":
        version = provider.latest_version(platform, log)
        log.step(f"Latest available for {platform} via '{provider.name}': PostgreSQL {version}")
    else:
        log.step(f"Using PostgreSQL {version} for {platform} via '{provider.name}'")

    transient = work_dir is None
    if transient:
        work_dir = Path(tempfile.mkdtemp(prefix="pgportable-build-"))
    else:
        work_dir = Path(work_dir).expanduser()
        work_dir.mkdir(parents=True, exist_ok=True)
    log.debug(f"work directory: {work_dir}")

    try:
        pgsql = provider.fetch(version, platform, work_dir, log)
        archive = work_dir / f"postgresql-{version}-{platform}.tar.{compression}"
        if archive.is_file() and archive.stat().st_size > 0:
            log.info(f"Reusing existing pack {archive.name} ({human_bytes(archive.stat().st_size)})")
        else:
            make_archive(pgsql, archive, compression, log)

        out_dir = Path(out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        exe = _run_pyinstaller(work_dir, name, out_dir, onedir, log)

        info = payload_mod.append_payload(
            target=exe,
            archive=archive,
            version=version,
            platform=platform,
            compression=compression,
        )
        payload_mod.write_meta(exe.with_name(exe.name + ".pgpayload.json"), info)
        log.step(
            f"Embedded PostgreSQL {version} ({human_bytes(info.length)}) into {exe.name}"
        )
        return exe
    finally:
        if keep_work:
            log.info(f"Work directory kept: {work_dir}")
        elif transient:
            shutil.rmtree(work_dir, ignore_errors=True)
