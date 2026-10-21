"""pgportable - build a single-file portable PostgreSQL that can load pg_dump files."""

from pgportable.config import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
__version__ = APP_VERSION
