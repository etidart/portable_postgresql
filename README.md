# pgportable

Build a **single-file portable PostgreSQL** that needs no installation, creates its
database in a temporary directory, and can **load a `pg_dump` file** on startup. When
it shuts down it writes a dump back out, then deletes the temporary data directory.

```
pgportable build            # -> dist/pgportable.exe  (Windows)  or  dist/pgportable (Linux)
./dist/pgportable --load mydb.dump --psql
```

---

## What you get

* **One file.** A PyInstaller `--onefile` launcher with the PostgreSQL tree appended
  as a trailer, so nothing has to be unpacked by hand.
* **Cached, version-checked extraction.** The first run extracts PostgreSQL into a
  cache directory keyed by `"<pg-version>-<platform>"`. Later runs reuse it; a build
  carrying a different PostgreSQL version re-extracts automatically.
* **Ephemeral database.** `initdb` runs into a fresh temp directory, the server binds
  to `127.0.0.1` only, and the data directory is removed on exit.
* **Dump in, dump out.** Load `.sql`, `.dump`, `.tar`, or directory archives (plain,
  gzipped, xz, bzip2, zstd) on start; get a fresh dump written on shutdown.
* **All formats on both ends.** `psql` for plain SQL, `pg_restore` for custom/tar/
  directory archives.

## How the single file works

PostgreSQL is a native multi-file tree, so it must exist on disk to run. `pgportable`
handles that transparently:

```
[ launcher: .exe / ELF ]
[ tar.gz of pgsql/ ]
[ 72-byte footer: magic | offset | length | compression | platform | version ]
```

On start the footer is read with a single seek. If the cache already holds the same
`version-platform` key, the payload is never touched again. Otherwise it is extracted
to a staging directory and atomically renamed into place, so two concurrent runs
cannot corrupt each other.

Cache locations (override with `--cache-dir`):

| OS | Path |
| --- | --- |
| Windows | `%LOCALAPPDATA%\pgportable\<version>-<platform>\` |
| Linux/macOS | `${XDG_CACHE_HOME:-~/.cache}/pgportable/<version>-<platform>/` |

## Requirements

* Python 3.10+ to build. The produced artifact has **no** Python requirement.
* `pyinstaller` to build (`pip install pyinstaller`), plus network access to download
  PostgreSQL binaries.

## Building

```bash
pip install -e ".[build]"
python build.py                       # latest PostgreSQL available, current OS
python build.py --pg-version 17.2     # pin a version
python build.py --compression xz      # smaller payload, slower pack
python build.py --onedir              # folder + .pgpayload sidecar instead of one file
```

`--pg-version latest` (the default) asks the selected source for its newest release,
so builds stay current without code changes.

### Binary sources

| Platform | Source | Notes |
| --- | --- | --- |
| Windows x64 | `edb` (EnterpriseDB) | `postgresql-<ver>-1-windows-x64-binaries.zip`; full build including contrib |
| Linux x64/arm64 | `theseus` | `theseus-rs/postgresql-binaries` GitHub releases; full build with `psql`, `pg_dump`, `pg_restore`, `pg_isready` |
| macOS x64/arm64 | `theseus` | same project, `apple-darwin` targets |

EnterpriseDB stopped publishing modern **Linux** binary archives (they end at
PostgreSQL 9.3), and zonky's `io.zonky.test.postgres` builds ship only the
server (`initdb`, `pg_ctl`, `postgres`) with no client tools, so Linux and
macOS use `theseus`, which ships the complete distribution (PostgreSQL 12+).
Use `--source edb` or `--source theseus` to force one.

### Cross-compilation

PyInstaller cannot cross-compile. A Windows `.exe` must be built on Windows and a
Linux binary on Linux. The included GitHub Actions workflow does both.

## Running

```bash
# serve until Ctrl+C, dumping the database back to ./pgdump-<timestamp>.sql on exit
./dist/pgportable

# load a dump, then drop into psql
./dist/pgportable --load backup.dump --psql

# restore, run one query, exit (exit dump still written)
./dist/pgportable --load backup.sql --exec "SELECT count(*) FROM users"

# keep serving after a query, dump everything, custom format
./dist/pgportable --exec "SELECT 1" --serve --dump-scope all --dump-format custom
```

On startup the connection details are printed; by default this is
`postgresql://postgres@127.0.0.1:5432/postgres` with **trust** authentication on
loopback only. Pass `--password` to switch `initdb` to `scram-sha-256`, or `--port`
to move off 5432.

### Loading dumps

Format is auto-detected, including compressed inputs:

| Input | Detected as | Loaded with |
| --- | --- | --- |
| `.sql`, plain text | plain | `psql -v ON_ERROR_STOP=1 -f` |
| `PGDMP` magic | custom (`.dump`) | `pg_restore --no-owner --no-privileges` |
| `ustar` header at offset 257 | tar | `pg_restore -F t` |
| directory containing `toc.dat` | directory | `pg_restore -F d` |
| `.gz` / `.xz` / `.bz2` / `.lzma` | decompressed first, then as above | |
| `.zst` / `.zstd` | needs the optional `zstandard` package | |

Plain dumps that contain `CREATE DATABASE` or `\connect` (e.g. `pg_dump -C`,
`pg_dumpall` output) are applied against the `postgres` database as-is; otherwise the
target database is created first. Use `--ignore-errors` for dumps with known-benign
failures and `--clean` to emit `--clean --if-exists`.

### The exit dump

On normal exit, `SIGINT`/`SIGTERM`, or `SIGBREAK` the server is dumped **before** it
stops, then the data directory is deleted.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--dump-format` | `plain` | `plain`, `custom`, `tar`, `directory` |
| `--dump-scope` | `db` | `db` (one database) or `all` (globals + every database) |
| `--dump-out` | `pgdump-<timestamp>.<ext>` in cwd | explicit destination |
| `--no-dump-out` | off | disable the exit dump entirely |
| `--force-dump` | off | overwrite an existing destination |

`--dump-scope all` with `plain` uses `pg_dumpall`. With `custom`/`tar`/`directory` it
writes a directory containing `globals.sql` plus one archive per database.

A hard kill (`SIGKILL`, power loss) cannot run a dump or clean up; use
`--keep-data` while debugging to inspect what was left behind.

## CLI reference

```
pgportable build   [--pg-version V] [--source auto|edb|theseus] [--out DIR] [--name N]
                   [--compression gz|xz] [--work-dir DIR] [--keep-work] [--onedir]
                   [--platform P] [--log-level ...]

pgportable run     [--load PATH ...] [--db DB] [--user U] [--password P] [--port N] [--host H]
                   [--psql] [--exec SQL|FILE ...] [--serve]
                   [--dump-out PATH] [--no-dump-out] [--dump-format ...] [--dump-scope db|all]
                   [--force-dump] [--clean] [--ignore-errors]
                   [--cache-dir DIR] [--pgsql-dir DIR] [--keep-data] [--log-level ...]

pgportable cache   path|list|prune [--cache-dir DIR]
pgportable version
```

Running the built executable with no subcommand implies `run`, so flags can be passed
directly.

## Platform notes

**Windows.** EnterpriseDB archives are self-contained, but if the server fails to
start with a missing-DLL error, install the
[Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).
An unsigned one-file executable may trigger SmartScreen or antivirus scanning.

**Linux.** `initdb` refuses to run as `root`; run as an unprivileged user. The cache
directory must allow execution (not on a `noexec` mount) - use `--cache-dir` if needed.
The theseus builds bundle their shared libraries next to the binaries, so no system
PostgreSQL packages are required.

**`pg_dump`/`pg_restore` version.** The client tools bundled in the artifact must be at
least as new as the server that produced a dump. Dump with `--pg-version` set to that
server's major version or newer.

## Development

```bash
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest          # unit tests, no downloads
python -m pgportable version
```

Integration tests start a real server and are skipped unless a PostgreSQL tree is
pointed at (they then skip downloads and use an alternate port).

```bash
# Windows
set PGPORTABLE_PGSQL_DIR=%LOCALAPPDATA%\pgportable\18.6-windows-x64\pgsql
# Linux
export PGPORTABLE_PGSQL_DIR=~/.cache/pgportable/18.6-linux-amd64/pgsql

python -m pytest -q                      # unit + integration
```

You can also exercise the runtime without building an artifact at all:

```bash
python -m pgportable run --pgsql-dir /path/to/pgsql --exec "SELECT version()"
```

## Layout

```
src/pgportable/
  cli.py        argparse front-end (build / run / cache / version)
  builder.py    fetch -> pack -> PyInstaller -> append payload
  sources/      edb.py (Windows), theseus.py (Linux/macOS/Windows), base.py
  bundle.py     download + archive extraction helpers
  payload.py    trailer pack/read, extraction, permission fixups
  cache.py      version-keyed extraction cache
  instance.py   initdb / configure / start / stop, client helpers
  loader.py     dump format detection and restore
  dumper.py     exit dump
```
