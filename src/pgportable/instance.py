"""Lifecycle of the throw-away PostgreSQL server.

Everything lives outside the extracted ``pgsql`` tree: ``initdb`` writes into a
fresh temporary data directory, the server binds to localhost only, and on
shutdown the data directory is deleted (after the optional exit dump).
"""

from __future__ import annotations

import locale
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from pgportable.config import (
    BUILTIN_LOCALE_MIN_MAJOR,
    DEFAULT_DB,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SUPERUSER,
    LOCALE_PREFERENCE,
    exe_suffix,
    is_root,
    parse_major,
    sanitize_child_env,
)
from pgportable.errors import InstanceError
from pgportable.ui import Log


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _decode(raw: bytes) -> str:
    """Decode server output, which uses the platform code page on Windows."""
    seen: list[str] = []
    for codec in ("utf-8", locale.getpreferredencoding(False), "cp866", "cp1251"):
        if codec and codec.lower() not in seen:
            seen.append(codec.lower())
            try:
                return raw.decode(codec)
            except (UnicodeDecodeError, LookupError):
                continue
    return raw.decode("utf-8", "replace")


class Instance:
    def __init__(
        self,
        pgsql_dir: Path,
        data_dir: Path,
        log: Log,
        port: int = DEFAULT_PORT,
        user: str = DEFAULT_SUPERUSER,
        password: str | None = None,
        host: str = DEFAULT_HOST,
    ) -> None:
        self.pgsql_dir = Path(pgsql_dir)
        self.data_dir = Path(data_dir)
        self.log = log
        self.port = int(port)
        self.user = user
        self.password = password
        self.host = host
        self._started = False
        self._socket_dir = Path(tempfile.gettempdir()) / f"pgportable-sock-{os.getpid()}"
        self.log_file = Path(tempfile.gettempdir()) / f"pgportable-server-{os.getpid()}.log"
        # pg_ctl output must go somewhere other than log_file: the postmaster
        # opens log_file itself and a second handle would collide on Windows.
        self.ctl_log_file = Path(tempfile.gettempdir()) / f"pgportable-pgctl-{os.getpid()}.log"
        self._version: str | None = None
        self._ever_started = False

    @property
    def started(self) -> bool:
        return self._started

    # -- helpers -----------------------------------------------------------
    def _bin(self, name: str) -> Path:
        suffix = exe_suffix()
        candidates = [self.pgsql_dir / "bin" / f"{name}{suffix}"]
        if suffix:
            candidates.append(self.pgsql_dir / "bin" / name)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise InstanceError(f"required PostgreSQL binary not found: {name} (in {self.pgsql_dir / 'bin'})")

    def env(self) -> dict:
        env = os.environ.copy()
        sanitize_child_env(env)
        if self.password:
            env["PGPASSWORD"] = self.password
        env["PGCLIENTENCODING"] = "UTF8"
        if os.name != "nt":
            env.setdefault("LC_ALL", "C.UTF-8")
        return env

    def _run(
        self,
        cmd,
        capture: bool = False,
        check: bool = True,
        env_extra: dict | None = None,
    ) -> subprocess.CompletedProcess:
        command = [str(part) for part in cmd]
        environment = self.env()
        if env_extra:
            environment.update(env_extra)
        self.log.debug("$ " + " ".join(command))
        result = subprocess.run(command, env=environment, capture_output=capture, text=True)
        if check and result.returncode != 0:
            detail = ((result.stderr or "") + (result.stdout or "")).strip()
            raise InstanceError(
                f"command failed with exit code {result.returncode}: {' '.join(command)}"
                + (f"\n{detail}" if detail else "")
            )
        return result

    def server_version(self) -> str:
        if self._version is None:
            result = self._run([self._bin("postgres"), "--version"], capture=True)
            self._version = result.stdout.strip().split()[-1]
        return self._version

    # -- initdb ------------------------------------------------------------
    def _initdb_strategies(self) -> list[tuple[list[str], dict | None]]:
        major = parse_major(self.server_version())
        strategies: list[tuple[list[str], dict | None]] = []

        if major >= BUILTIN_LOCALE_MIN_MAJOR:
            strategies.append(
                (["--locale-provider=builtin", "--builtin-locale=C.UTF-8"], None)
            )
        for locale in LOCALE_PREFERENCE:
            env = None
            if os.name != "nt":
                env = {"LC_ALL": locale, "LANG": locale}
            strategies.append(([f"--locale={locale}"], env))
        return strategies

    def initdb(self) -> None:
        if is_root():
            raise InstanceError(
                "PostgreSQL refuses to run as root. Re-run as an unprivileged user "
                "(e.g. create a dedicated user, or use `su - postgres` / `runuser -u nobody`)."
            )

        auth = ["--auth-local=trust", "--auth-host=trust"]
        pwfile: Path | None = None
        if self.password:
            auth = ["--auth-local=scram-sha-256", "--auth-host=scram-sha-256"]
            handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
            handle.write(self.password + "\n")
            handle.close()
            pwfile = Path(handle.name)
            auth.append(f"--pwfile={pwfile}")

        failures: list[str] = []
        try:
            for extra, env in self._initdb_strategies():
                if self.data_dir.exists():
                    shutil.rmtree(self.data_dir, ignore_errors=True)
                self.data_dir.parent.mkdir(parents=True, exist_ok=True)

                cmd = [
                    self._bin("initdb"),
                    "-D",
                    self.data_dir,
                    "-U",
                    self.user,
                    "--encoding=UTF8",
                    "--no-instructions",
                    *auth,
                    *extra,
                ]
                self.log.debug("initdb locale attempt: " + " ".join(extra) if extra else "")
                try:
                    self._run(cmd, capture=True, env_extra=env)
                    self.log.info(
                        f"Initialised data directory ({' '.join(extra) or 'default locale'})"
                    )
                    return
                except InstanceError as exc:
                    failures.append(f"{' '.join(extra) or 'default'}: {exc}")
                    self.log.debug(f"initdb attempt failed ({' '.join(extra)}): {exc}")
        finally:
            if pwfile:
                pwfile.unlink(missing_ok=True)

        raise InstanceError(
            "initdb failed for every locale strategy:\n  " + "\n  ".join(failures)
        )

    # -- configuration -----------------------------------------------------
    def configure(self) -> None:
        conf = self.data_dir / "postgresql.conf"
        if not conf.is_file():
            raise InstanceError(f"missing postgresql.conf in {self.data_dir}")

        lines = [f"listen_addresses = '{self.host}'", f"port = {self.port}"]
        if os.name != "nt":
            self._socket_dir.mkdir(parents=True, exist_ok=True)
            lines.append(f"unix_socket_directories = '{self._socket_dir.as_posix()}'")

        with open(conf, "a", encoding="utf-8") as handle:
            handle.write("\n# ---- pgportable ----\n")
            handle.write("\n".join(lines) + "\n")

    def _run_pg_ctl(self, args, check: bool = True) -> None:
        """Run ``pg_ctl`` with output redirected to a file.

        Never use pipes here: on Windows the postmaster and its background
        workers inherit ``pg_ctl``'s stdout/stderr handles and keep them open,
        so a capturing ``subprocess`` call would block until the server exits.
        """
        command = [str(part) for part in args]
        self.log.debug("$ " + " ".join(command))
        self.ctl_log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ctl_log_file, "a", encoding="utf-8", errors="replace") as sink:
            result = subprocess.run(
                command,
                env=self.env(),
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
            )
        if check and result.returncode != 0:
            raise InstanceError(
                f"pg_ctl failed with exit code {result.returncode}: {' '.join(command)}"
            )

    # -- start / stop ------------------------------------------------------
    def start(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._run_pg_ctl(
                [
                    self._bin("pg_ctl"),
                    "-D",
                    self.data_dir,
                    "-l",
                    self.log_file,
                    "-w",
                    "-t",
                    "60",
                    "start",
                ]
            )
        except InstanceError as exc:
            raise InstanceError(f"could not start PostgreSQL.\n{exc}\n{self._log_tail()}") from exc

        self._started = True
        self._ever_started = True
        self.wait_ready()

    def wait_ready(self, timeout: float = 30.0) -> None:
        import time

        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            result = self._run(
                [
                    self._bin("pg_isready"),
                    "-h",
                    self.host,
                    "-p",
                    str(self.port),
                    "-U",
                    self.user,
                ],
                capture=True,
                check=False,
            )
            if result.returncode == 0:
                self.log.debug("server is accepting connections")
                return
            last = (result.stdout or "").strip()
            time.sleep(0.25)
        raise InstanceError(f"server did not become ready within {timeout:.0f}s ({last})\n{self._log_tail()}")

    def stop(self, mode: str = "fast") -> None:
        if not self._started:
            return
        self._run_pg_ctl(
            [
                self._bin("pg_ctl"),
                "-D",
                self.data_dir,
                "-m",
                mode,
                "-w",
                "-t",
                "60",
                "stop",
            ],
            check=False,
        )
        self._started = False

    def cleanup(self) -> None:
        shutil.rmtree(self.data_dir, ignore_errors=True)
        shutil.rmtree(self._socket_dir, ignore_errors=True)
        # Keep the logs around when the server never came up, so start-up
        # failures stay diagnosable.
        if self._ever_started:
            self.log_file.unlink(missing_ok=True)
            self.ctl_log_file.unlink(missing_ok=True)

    def _log_tail(self, lines: int = 25) -> str:
        sections: list[str] = []
        for label, path in (("server", self.log_file), ("pg_ctl", self.ctl_log_file)):
            if not path.is_file():
                continue
            try:
                content = _decode(path.read_bytes()).splitlines()
            except OSError:
                continue
            if content:
                sections.append(f"--- {label} log ({path}) ---\n" + "\n".join(content[-lines:]))
        return "\n".join(sections)

    # -- client helpers ----------------------------------------------------
    def psql_cmd(self, db: str | None = None, extra=()) -> list:
        cmd = [
            self._bin("psql"),
            "-h",
            self.host,
            "-p",
            str(self.port),
            "-U",
            self.user,
            "-v",
            "ON_ERROR_STOP=1",
        ]
        if db:
            cmd += ["-d", db]
        return cmd + list(extra)

    def query(self, sql: str, db: str = DEFAULT_DB) -> str:
        result = self._run(self.psql_cmd(db, ["-tAc", sql]), capture=True)
        return result.stdout.strip()

    def database_exists(self, name: str) -> bool:
        sql = f"SELECT 1 FROM pg_database WHERE datname = {sql_literal(name)}"
        return self.query(sql) == "1"

    def ensure_database(self, name: str) -> None:
        if self.database_exists(name):
            return
        self.log.info(f"Creating database {name}")
        self._run(
            [
                self._bin("createdb"),
                "-h",
                self.host,
                "-p",
                str(self.port),
                "-U",
                self.user,
                name,
            ],
            capture=True,
        )

    def list_databases(self) -> list[str]:
        out = self.query(
            "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"
        )
        return [line for line in out.splitlines() if line]

    def connection_info(self, db: str = DEFAULT_DB) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "dbname": db,
            "password": self.password,
        }

    def url(self, db: str = DEFAULT_DB) -> str:
        auth = f"{self.user}:{self.password}" if self.password else self.user
        return f"postgresql://{auth}@{self.host}:{self.port}/{db}"
