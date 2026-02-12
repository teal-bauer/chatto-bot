"""CLI entry point: python -m chatto_bot run mybot.py"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import signal
import sys
from pathlib import Path


def _load_bot_script(script_path: str) -> None:
    """Load and execute a bot script."""
    path = Path(script_path).resolve()
    if not path.exists():
        print(f"Error: {script_path} not found", file=sys.stderr)
        sys.exit(1)

    # Add script's directory to sys.path so relative imports work
    script_dir = str(path.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    spec = importlib.util.spec_from_file_location("__bot__", path)
    if not spec or not spec.loader:
        print(f"Error: could not load {script_path}", file=sys.stderr)
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print("Usage: python -m chatto_bot run <script.py>")
        print("       python -m chatto_bot --help")
        sys.exit(0)

    if args[0] == "run":
        if len(args) < 2:
            print("Usage: python -m chatto_bot run <script.py>", file=sys.stderr)
            sys.exit(1)
        _load_bot_script(args[1])
    else:
        # Assume it's a script path
        _load_bot_script(args[0])


if __name__ == "__main__":
    main()
