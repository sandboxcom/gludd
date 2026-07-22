#!/usr/bin/env python3
"""Print an inclusive line range from an allowed workspace file."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _is_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return False
    root = ROOT.resolve()
    resolved_text = str(resolved)
    return (
        resolved == root
        or root in resolved.parents
        or resolved_text.startswith("/tmp/gludd-")
        or resolved_text.startswith("/private/tmp/gludd-")
    )


def main() -> int:
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <file> <start> <end>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not _is_allowed(path):
        print(f"ERROR: refusing to read outside workspace or /tmp/gludd-*: {path}", file=sys.stderr)
        return 1
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    if start < 1 or end < start:
        print("ERROR: invalid line range", file=sys.stderr)
        return 1
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if start <= number <= end:
            print(f"{number}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
