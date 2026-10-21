"""Command line interface: ``pgportable build`` and ``pgportable run``."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import tempfile
import threading
from pathlib import Path

from pgportable import __version__, builder, cache as cache_mod, dumper, loader
from pgportable import payload as payload_mod
from pgportable.config import (
    APP_NAME,
    DEFAULT_DB,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SUPERUSER,
    cache_root,
    current_platform,
)
from pgportable.errors import PayloadError, PgPortableError
from pgportable.instance import Instance
from pgportable.ui import Log, human_bytes

KNOWN_COMMANDS = {"build", "run", "cache", "version"}


# --------------------------------------------------------------------------- CLI
def _add_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--load",
        action="append",
        default=[],
        metavar="PATH",
        help="dump to restore after startup (.sql/.dump/.tar/.dir, optionally .gz/.xz)",
    )
    parser.add_argument("--db", default=None, help="target database for --load / --exec")
    parser.add_argument("--user", default=DEFAULT_SUPERUSER, help="superuser name")
    parser.add_argument("--password", default=None, help="superuser password (default: trust auth)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument("--host", default=DEFAULT_HOST, help="listen address (default: localhost)")

    parser.add_argument("--psql", action="store_true", help="open an interactive psql shell")
    parser.add_argument(
        "--exec",
        dest="exec_cmd",
        action="append",
        default=[],
        metavar="SQL|FILE",
        help="run SQL (or a .sql file) and continue; repeatable",
    )
    parser.add_argument("--serve", action="store_true", help="keep serving after --exec/--psql")

    parser.add_argument("--dump-out", default=None, metavar="PATH", help="exit dump destination")
    parser.add_argument("--no-dump-out", action="store_true", help="disable the exit dump")
    parser.add_argument(
        "--dump-format",
        choices=("plain", "custom", "tar", "directory"),
        default="plain",
        help="exit dump format (default: plain SQL)",
    )
    parser.add_argument(
        "--dump-scope", choices=("db", "all"), default="db", help="dump one database or all"
    )
    parser.add_argument("--force-dump", action="store_true", help="overwrite an existing dump")
    parser.add_argument(
        "--clean", action="store_true", help="pg_restore --clean --if-exists when loading"
    )
    parser.add_argument(
        "--ignore-errors", action="store_true", help="continue past restore errors"
    )

    parser.add_argument("--cache-dir", default=None, help="override the extraction cache root")
    parser.add_argument(
        "--pgsql-dir", default=None, help="use an existing extracted PostgreSQL tree (skips the payload)"
    )
    parser.add_argument("--keep-data", action="store_true", help="keep the temp data directory")
    parser.add_argument(
        "--log-level", choices=("debug", "info", "warn", "error"), default="info"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Build a single-file portable PostgreSQL that can load pg_dump files.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command")

    build_cmd = sub.add_parser("build", help="download PostgreSQL and produce the executable")
    build_cmd.add_argument(
        "--pg-version", default="latest", help="PostgreSQL version (default: latest available)"
    )
    build_cmd.add_argument(
        "--source", choices=("auto", "edb", "theseus"), default="auto", help="binary provider"
    )
    build_cmd.add_argument("--out", default="dist", help="output directory")
    build_cmd.add_argument("--name", default=APP_NAME, help="artifact name")
    build_cmd.add_argument("--compression", choices=("gz", "xz"), default="gz")
    build_cmd.add_argument("--work-dir", default=None, help="keep build scratch files here")
    build_cmd.add_argument("--keep-work", action="store_true", help="do not delete the work dir")
    build_cmd.add_argument("--onedir", action="store_true", help="produce a folder instead of one file")
    build_cmd.add_argument("--platform", default=None, help="override the target platform")
    build_cmd.add_argument(
        "--log-level", choices=("debug", "info", "warn", "error"), default="info"
    )

    run_cmd = sub.add_parser("run", help="start the embedded PostgreSQL")
    _add_run_options(run_cmd)

    cache_cmd = sub.add_parser("cache", help="inspect or prune the extraction cache")
    cache_cmd.add_argument("cache_action", choices=("path", "list", "prune"))
    cache_cmd.add_argument("--cache-dir", default=None)
    cache_cmd.add_argument("--log-level", choices=("debug", "info", "warn", "error"), default="info")

    version_cmd = sub.add_parser("version", help="print version information")
    version_cmd.add_argument("--log-level", choices=("debug", "info", "warn", "error"), default="info")

    return parser


def _inject_default_command(argv: list[str]) -> list[str]:
    if not argv:
        return ["run", "--help"]
    first = argv[0]
    if first in KNOWN_COMMANDS or first in ("-h", "--help") or first == "--version":
        return argv
    if first.startswith("-"):
        return ["run", *argv]
    return argv


# ------------------------------------------------------------------------ run
def _install_signal_handlers() -> threading.Event:
    stop = threading.Event()

    def handler(signum, frame):  # noqa: ANN001
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, RuntimeError):
            pass
    return stop


def _resolve_pgsql(args, log: Log) -> Path:
    if args.pgsql_dir:
        pgsql = Path(args.pgsql_dir).expanduser().resolve()
        if not (pgsql / "bin").is_dir():
            raise PayloadError(f"--pgsql-dir does not contain a bin/ directory: {pgsql}")
        log.info(f"Using provided PostgreSQL tree {pgsql}")
        return pgsql

    info = payload_mod.locate_payload(log)
    if info is None:
        raise PayloadError(
            "no embedded PostgreSQL payload found.\n"
            "Build one with `pgportable build`, or point at an extracted tree with --pgsql-dir."
        )

    key = cache_mod.cache_key(info.version, info.platform)
    entry = cache_mod.entry_dir(info.version, info.platform, args.cache_dir)
    if cache_mod.is_ready(entry, key):
        log.info(f"Using cached PostgreSQL {info.version} ({info.platform}) at {entry}")
        return entry / "pgsql"

    log.step(f"Preparing PostgreSQL {info.version} ({info.platform})")
    entry.parent.mkdir(parents=True, exist_ok=True)
    staging = entry.with_name(entry.name + f".tmp-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        payload_mod.extract_payload(info, staging, log)
        cache_mod.write_marker(staging, key)
        if cache_mod.is_ready(entry, key):
            log.debug("another process populated the cache first")
            shutil.rmtree(staging, ignore_errors=True)
        else:
            shutil.rmtree(entry, ignore_errors=True)
            staging.rename(entry)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    log.info(f"Cached at {entry}")
    return entry / "pgsql"


def _run_psql_item(instance: Instance, db: str, item: str, log: Log) -> None:
    candidate = Path(item)
    if candidate.is_file():
        log.step(f"Running {candidate}")
        instance._run(instance.psql_cmd(db, ["-f", str(candidate)]))
    else:
        log.step(f"Running SQL: {item[:80]}{'...' if len(item) > 80 else ''}")
        instance._run(instance.psql_cmd(db, ["-c", item]))


def _run_psql_shell(instance: Instance, db: str, log: Log) -> None:
    import subprocess

    log.step(f"Opening psql on {db} (\\q or Ctrl+D to exit)")
    subprocess.run([str(part) for part in instance.psql_cmd(db)], env=instance.env())


def _serve(instance: Instance, db: str, log: Log, stop: threading.Event) -> None:
    log.info("")
    log.info(f"  PostgreSQL : {instance.server_version()}")
    log.info(f"  host       : {instance.host}")
    log.info(f"  port       : {instance.port}")
    log.info(f"  user       : {instance.user}")
    log.info(f"  database   : {db}")
    log.info(f"  url        : {instance.url(db)}")
    log.info("")
    log.info("Serve until Ctrl+C - the exit dump runs automatically on shutdown.")
    while not stop.is_set():
        stop.wait(0.5)


def cmd_run(args, log: Log) -> int:
    pgsql_dir = _resolve_pgsql(args, log)
    data_dir = Path(tempfile.mkdtemp(prefix="pgportable-data-"))
    instance = Instance(
        pgsql_dir=pgsql_dir,
        data_dir=data_dir,
        log=log,
        port=args.port,
        user=args.user,
        password=args.password,
        host=args.host,
    )
    stop = _install_signal_handlers()
    db = args.db or DEFAULT_DB

    try:
        instance.initdb()
        instance.configure()
        instance.start()
        log.info(
            f"PostgreSQL {instance.server_version()} ready on {instance.host}:{instance.port}"
        )

        for dump_file in args.load:
            db = loader.load(
                instance,
                Path(dump_file),
                db=args.db,
                log=log,
                ignore_errors=args.ignore_errors,
                clean=args.clean,
            )

        interacted = False
        for item in args.exec_cmd:
            interacted = True
            _run_psql_item(instance, db, item, log)
        if args.psql:
            interacted = True
            _run_psql_shell(instance, db, log)
        if not interacted or args.serve:
            _serve(instance, db, log, stop)
    finally:
        try:
            if instance.started and not args.no_dump_out:
                try:
                    written = dumper.dump(
                        instance,
                        fmt=args.dump_format,
                        scope=args.dump_scope,
                        out=Path(args.dump_out) if args.dump_out else None,
                        db=db,
                        force=args.force_dump,
                        log=log,
                    )
                    log.step(f"Exit dump written to {written}")
                except PgPortableError as exc:
                    log.error(f"exit dump failed: {exc}")
            instance.stop()
        finally:
            if args.keep_data:
                log.warn(f"Data directory kept: {instance.data_dir}")
            else:
                instance.cleanup()
    return 0


# ---------------------------------------------------------------------- build
def cmd_build(args, log: Log) -> int:
    exe = builder.build(
        pg_version=args.pg_version,
        source=args.source,
        out_dir=Path(args.out),
        name=args.name,
        compression=args.compression,
        work_dir=Path(args.work_dir) if args.work_dir else None,
        keep_work=args.keep_work,
        onedir=args.onedir,
        platform=args.platform,
        log=log,
    )
    size = human_bytes(exe.stat().st_size)
    log.step(f"Built {exe} ({size})")
    log.info("Run it: " + str(exe))
    return 0


# ---------------------------------------------------------------------- cache
def cmd_cache(args, log: Log) -> int:
    if args.cache_action == "path":
        print(cache_root(args.cache_dir))
        return 0
    if args.cache_action == "list":
        entries = cache_mod.list_entries(args.cache_dir)
        if not entries:
            print("(cache is empty)")
            return 0
        for entry in entries:
            marker = cache_mod.read_marker(entry) or {}
            print(f"{entry.name}\t{marker.get('created', '?')}\t{entry}")
        return 0
    removed = cache_mod.prune(args.cache_dir, log)
    log.step(f"Removed {removed} cache entr{'y' if removed == 1 else 'ies'}")
    return 0


# -------------------------------------------------------------------- version
def cmd_version(args, log: Log) -> int:
    print(f"{APP_NAME} {__version__}")
    print(f"platform: {current_platform()}")
    info = payload_mod.locate_payload(log)
    if info is None:
        print("payload : none (run from a built artifact or set PGPORTABLE_PAYLOAD)")
    else:
        print(f"payload : PostgreSQL {info.version} [{info.platform}, {info.compression}]")
        print(f"source  : {info.path}")
    return 0


# ----------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    argv = _inject_default_command(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    log = Log(getattr(args, "log_level", "info"))

    if args.command is None:
        parser.print_help()
        return 2

    handlers = {
        "run": cmd_run,
        "build": cmd_build,
        "cache": cmd_cache,
        "version": cmd_version,
    }
    try:
        return handlers[args.command](args, log)
    except PgPortableError as exc:
        log.error(str(exc))
        return exc.exit_code
    except KeyboardInterrupt:
        log.warn("interrupted")
        return 130
