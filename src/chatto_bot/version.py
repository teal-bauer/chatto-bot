"""Version detection with fallback chain.

1. _version.py (written by hatch-vcs at build/install time)
2. git describe (works in dev checkout)
3. VERSION file (written at deploy time)
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _get_version() -> str:
    # 1. Build-time version from hatch-vcs
    try:
        from chatto_bot._version import __version__

        return __version__
    except ImportError:
        pass

    # 2. Live git describe (dev checkout)
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--dirty", "--always"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 3. VERSION file (raw deploy)
    for path in (Path("VERSION"), Path(__file__).parent.parent.parent / "VERSION"):
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            pass

    return "unknown"


__version__ = _get_version()
