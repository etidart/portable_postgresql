"""Allow ``python -m pgportable``."""

from __future__ import annotations

from pgportable.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
