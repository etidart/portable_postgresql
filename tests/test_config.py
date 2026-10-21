from __future__ import annotations

import sys

from pgportable import builder
from pgportable.config import sanitize_child_env


def test_sanitize_child_env_untouched_when_not_frozen(monkeypatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    env = {"LD_LIBRARY_PATH": "/opt/custom/lib"}
    assert sanitize_child_env(env) == {"LD_LIBRARY_PATH": "/opt/custom/lib"}


def test_sanitize_child_env_removes_meipass_entry(monkeypatch, tmp_path) -> None:
    meipass = tmp_path / "_MEI123"
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    env = {"LD_LIBRARY_PATH": f"{meipass}:/opt/custom/lib"}
    sanitize_child_env(env)
    assert env["LD_LIBRARY_PATH"] == "/opt/custom/lib"


def test_sanitize_child_env_drops_empty_path(monkeypatch, tmp_path) -> None:
    meipass = tmp_path / "_MEI123"
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    env = {"LD_LIBRARY_PATH": str(meipass)}
    sanitize_child_env(env)
    assert "LD_LIBRARY_PATH" not in env


def test_sanitize_child_env_handles_darwin_and_preload(monkeypatch, tmp_path) -> None:
    meipass = tmp_path / "_MEI123"
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    env = {
        "DYLD_LIBRARY_PATH": f"{meipass}:/opt/custom/lib",
        "DYLD_FALLBACK_LIBRARY_PATH": str(meipass),
        "LD_PRELOAD": f"{meipass}/libreadline.so.8",
    }
    sanitize_child_env(env)
    assert env["DYLD_LIBRARY_PATH"] == "/opt/custom/lib"
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in env
    assert "LD_PRELOAD" not in env


def test_readline_is_excluded_from_the_frozen_build() -> None:
    assert "readline" in builder.EXCLUDED_MODULES
