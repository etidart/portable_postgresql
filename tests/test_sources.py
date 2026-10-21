from __future__ import annotations

import pytest

from pgportable.errors import SourceError
from pgportable.sources import PROVIDERS, TheseusProvider, select_provider
from pgportable.sources.theseus import TARGETS, pg_version
from pgportable.ui import Log


def _log() -> Log:
    return Log("error")


def _provider_with(tags: list[str]) -> TheseusProvider:
    provider = TheseusProvider()
    provider._tags_cache = list(tags)
    return provider


def test_pg_version_strips_build_revision() -> None:
    assert pg_version("17.5.1") == "17.5"
    assert pg_version("18.6.0") == "18.6"
    assert pg_version("12.0.3") == "12.0"


def test_pg_version_passes_through_unknown_tags() -> None:
    assert pg_version("nightly") == "nightly"


def test_latest_version_is_newest_pg_release() -> None:
    provider = _provider_with(["16.9.1", "17.5.1", "18.6.1", "18.4.0"])
    assert provider.latest_version("linux-amd64", _log()) == "18.6"


def test_resolve_tag_prefers_newest_revision() -> None:
    provider = _provider_with(["17.5.0", "17.5.1", "17.4.0"])
    assert provider._resolve_tag("17.5", "linux-amd64", _log()) == "17.5.1"


def test_resolve_tag_accepts_exact_tag() -> None:
    provider = _provider_with(["17.5.0", "17.5.1"])
    assert provider._resolve_tag("17.5.0", "linux-amd64", _log()) == "17.5.0"


def test_resolve_tag_reports_missing_version() -> None:
    provider = _provider_with(["17.5.1", "18.6.1"])
    with pytest.raises(SourceError) as excinfo:
        provider._resolve_tag("11.22", "linux-amd64", _log())
    assert "11.22" in str(excinfo.value)
    assert "18.6" in str(excinfo.value)


def test_archive_url_uses_platform_target() -> None:
    provider = TheseusProvider()
    url = provider.archive_url("17.5.1", "linux-amd64")
    assert url == (
        "https://github.com/theseus-rs/postgresql-binaries/releases/download/"
        "17.5.1/postgresql-17.5.1-x86_64-unknown-linux-gnu.tar.gz"
    )


def test_all_targets_covered() -> None:
    assert set(TARGETS) == {
        "linux-amd64",
        "linux-arm64",
        "macos-x64",
        "macos-arm64",
        "windows-x64",
        "windows-arm64",
    }


def test_auto_selects_edb_on_windows_and_theseus_elsewhere() -> None:
    assert select_provider("auto", "windows-x64", _log()).name == "edb"
    assert select_provider("auto", "linux-amd64", _log()).name == "theseus"
    assert select_provider("auto", "macos-arm64", _log()).name == "theseus"


def test_unknown_source_lists_theseus() -> None:
    with pytest.raises(SourceError) as excinfo:
        select_provider("zonky", "linux-amd64", _log())
    assert "theseus" in str(excinfo.value)


def test_providers_are_instantiated() -> None:
    names = {provider.name for provider in PROVIDERS}
    assert names == {"edb", "theseus"}
