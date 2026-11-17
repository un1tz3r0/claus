"""Command-line entry point (placeholder until M8)."""

from __future__ import annotations

import sys

from claus import __version__


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        print(f"claus {__version__}")
        return 0
    print("claus: not implemented yet", file=sys.stderr)
    return 2
