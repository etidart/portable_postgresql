"""Tiny dependency-free logger with an in-place progress line."""

from __future__ import annotations

import sys
import time

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40, "quiet": 100}


def human_bytes(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < 1024.0 or unit == "TiB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TiB"


class Log:
    def __init__(self, level: str = "info", stream=None) -> None:
        self.level = _LEVELS.get(level, _LEVELS["info"])
        self.stream = stream or sys.stderr
        self._progress_active = False
        self._last_progress = 0.0

    # -- internals ---------------------------------------------------------
    def _enabled(self, level: str) -> bool:
        return _LEVELS[level] >= self.level

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self.stream, "encoding", None) or "utf-8"
            self.stream.write(text.encode(encoding, "replace").decode(encoding, "replace"))
        self.stream.flush()

    def _finish_progress(self) -> None:
        if self._progress_active:
            self._write("\n")
            self._progress_active = False

    def _line(self, tag: str, message: str) -> None:
        self._finish_progress()
        self._write(f"{tag}{message}\n")

    # -- public api --------------------------------------------------------
    def debug(self, message: str) -> None:
        if self._enabled("debug"):
            self._line("[debug] ", message)

    def info(self, message: str) -> None:
        if self._enabled("info"):
            self._line("", message)

    def step(self, message: str) -> None:
        if self._enabled("info"):
            self._line("==> ", message)

    def warn(self, message: str) -> None:
        if self._enabled("warn"):
            self._line("[warn] ", message)

    def error(self, message: str) -> None:
        if self._enabled("error"):
            self._line("[error] ", message)

    def progress(self, done: int, total: int, prefix: str = "") -> None:
        """Draw/refresh a single-line progress indicator."""
        if not self._enabled("info"):
            return
        now = time.monotonic()
        finished = total > 0 and done >= total
        if not finished and now - self._last_progress < 0.15:
            return
        self._last_progress = now
        width = 28
        if total > 0:
            frac = max(0.0, min(1.0, done / total))
            filled = int(width * frac)
            bar = "#" * filled + "-" * (width - filled)
            text = f"\r{prefix}[{bar}] {frac * 100:5.1f}%  {human_bytes(done)}/{human_bytes(total)}"
        else:
            text = f"\r{prefix}{human_bytes(done)}"
        self._write(text)
        self._progress_active = True
        if finished:
            self._finish_progress()

    def done_progress(self) -> None:
        self._finish_progress()
