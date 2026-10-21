#!/usr/bin/env python
"""Convenience wrapper: build the portable PostgreSQL single-file artifact.

Equivalent to ``pgportable build <args>`` but works straight from a checkout
without installing the package::

    python build.py
    python build.py --pg-version 17.2 --name pgpg
    python build.py --pg-version latest --compression xz
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pgportable.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["build", *sys.argv[1:]]))
