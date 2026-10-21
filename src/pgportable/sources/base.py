"""Provider interface for PostgreSQL binary distributions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pgportable.ui import Log


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for version strings such as ``"9.6.24"`` or ``"18.6"``."""
    parts: list[int] = []
    for piece in version.replace("-", ".").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


class Provider(ABC):
    """Fetches a vendor's PostgreSQL binaries for a platform."""

    name: str = "abstract"
    platforms: tuple[str, ...] = ()

    def supports(self, platform: str) -> bool:
        return platform in self.platforms

    @abstractmethod
    def latest_version(self, platform: str, log: Log) -> str:
        """Newest PostgreSQL version this provider offers for ``platform``."""

    @abstractmethod
    def archive_url(self, version: str, platform: str) -> str:
        """URL for ``version`` (also used for logging/diagnostics)."""

    @abstractmethod
    def fetch(self, version: str, platform: str, workdir: Path, log: Log) -> Path:
        """Download + unpack ``version`` and return the ready-to-use ``pgsql`` dir."""
