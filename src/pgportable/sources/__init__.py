"""Binary source providers."""

from __future__ import annotations

from pgportable.errors import SourceError
from pgportable.sources.base import Provider, version_key
from pgportable.sources.edb import EdbProvider
from pgportable.sources.theseus import TheseusProvider
from pgportable.ui import Log

PROVIDERS: tuple[Provider, ...] = (EdbProvider(), TheseusProvider())

__all__ = [
    "PROVIDERS",
    "EdbProvider",
    "Provider",
    "TheseusProvider",
    "select_provider",
    "version_key",
]


def select_provider(name: str, platform: str, log: Log) -> Provider:
    """Resolve ``name`` (``auto`` picks the first provider supporting ``platform``)."""
    if name in (None, "", "auto"):
        for provider in PROVIDERS:
            if provider.supports(platform):
                log.debug(f"auto-selected source '{provider.name}' for {platform}")
                return provider
        raise SourceError(
            f"no built-in source supports platform '{platform}'. "
            "Pass --source explicitly or use --pgsql-dir at runtime."
        )

    for provider in PROVIDERS:
        if provider.name == name:
            if not provider.supports(platform):
                supported = ", ".join(provider.platforms)
                raise SourceError(
                    f"source '{name}' does not support '{platform}' (supports: {supported})"
                )
            return provider

    known = ", ".join(p.name for p in PROVIDERS)
    raise SourceError(f"unknown source '{name}' (known: {known})")
