"""Frozen entry point used by the PyInstaller build.

Keeping this as a tiny module (instead of pointing PyInstaller at the package
``__main__``) avoids import-path surprises in the bundled executable.
"""

from pgportable.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
