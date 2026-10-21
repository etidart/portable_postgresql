"""Exit-time dump: capture the database before the temporary server goes away."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pgportable.config import DEFAULT_DB
from pgportable.errors import DumpError
from pgportable.instance import Instance
from pgportable.ui import Log

FORMAT_FLAGS = {"plain": "p", "custom": "c", "tar": "t", "directory": "d"}
FORMAT_EXT = {"plain": ".sql", "custom": ".dump", "tar": ".tar", "directory": ""}


def default_output(fmt: str, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    stamp = when.strftime("%Y%m%d-%H%M%S")
    ext = FORMAT_EXT.get(fmt, ".dump")
    return Path.cwd() / f"pgdump-{stamp}{ext}"


def _prepare(path: Path, force: bool, is_dir: bool) -> None:
    if is_dir and path.exists() and not path.is_dir():
        raise DumpError(f"{path} exists and is not a directory")
    if not is_dir and path.exists() and not path.is_file():
        raise DumpError(f"{path} exists and is not a regular file")
    if path.exists() and path.is_dir() and any(path.iterdir()):
        if not force:
            raise DumpError(f"{path} exists and is not empty (use --force-dump)")
    elif path.exists() and path.is_file() and path.stat().st_size > 0:
        if not force:
            raise DumpError(f"{path} already exists (use --force-dump to overwrite)")


def _pg_dump_to(instance: Instance, db: str, fmt: str, target: Path) -> None:
    command = [
        instance._bin("pg_dump"),
        "-h",
        instance.host,
        "-p",
        str(instance.port),
        "-U",
        instance.user,
        "-d",
        db,
        f"-F{FORMAT_FLAGS[fmt]}",
        "-f",
        str(target),
    ]
    instance._run(command, capture=True)


def dump(
    instance: Instance,
    fmt: str = "plain",
    scope: str = "db",
    out: Path | None = None,
    db: str | None = None,
    force: bool = False,
    log: Log | None = None,
) -> Path:
    """Write a dump of the running instance and return the path produced."""
    log = log or instance.log
    if fmt not in FORMAT_FLAGS:
        raise DumpError(f"unknown dump format '{fmt}' (plain, custom, tar, directory)")

    target = Path(out).expanduser() if out else default_output(fmt)
    target.parent.mkdir(parents=True, exist_ok=True)

    if scope == "db":
        _prepare(target, force, is_dir=(fmt == "directory"))
        database = db or DEFAULT_DB
        log.step(f"Dumping database '{database}' to {target}")
        _pg_dump_to(instance, database, fmt, target)
        return target

    if scope != "all":
        raise DumpError(f"unknown dump scope '{scope}' (db, all)")

    # scope=all
    if fmt == "plain":
        _prepare(target, force, is_dir=False)
        log.step(f"Dumping all databases (incl. globals) to {target}")
        instance._run(
            [
                instance._bin("pg_dumpall"),
                "-h",
                instance.host,
                "-p",
                str(instance.port),
                "-U",
                instance.user,
                "-f",
                str(target),
            ],
            capture=True,
        )
        return target

    target.mkdir(parents=True, exist_ok=True)
    _prepare(target, force, is_dir=True)
    log.step(f"Dumping globals and all databases into {target}")

    globals_file = target / "globals.sql"
    instance._run(
        [
            instance._bin("pg_dumpall"),
            "-h",
            instance.host,
            "-p",
            str(instance.port),
            "-U",
            instance.user,
            "--globals-only",
            "-f",
            str(globals_file),
        ],
        capture=True,
    )

    databases = instance.list_databases()
    if not databases:
        log.warn("no databases found to dump")
    for name in databases:
        ext = FORMAT_EXT[fmt]
        destination = target / f"{name}{ext}"
        log.info(f"Dumping database '{name}'")
        _pg_dump_to(instance, name, fmt, destination)
    return target
