"""Exception hierarchy for pgportable."""

from __future__ import annotations


class PgPortableError(Exception):
    """Base class for all expected, user-facing failures."""

    exit_code = 1


class SourceError(PgPortableError):
    """Could not resolve or download PostgreSQL binaries."""


class BundlingError(PgPortableError):
    """Could not assemble or package the portable artifact."""


class PayloadError(PgPortableError):
    """The embedded payload is missing or malformed."""


class InstanceError(PgPortableError):
    """initdb / start / stop of the temporary server failed."""


class RestoreError(PgPortableError):
    """Loading a dump file failed."""


class DumpError(PgPortableError):
    """Producing the exit dump failed."""
