#!/usr/bin/env python3
"""Create an allowed directory tree for make-only workflows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve_for_creation(path: Path) -> Path:
    try:
        return path.resolve()
    except FileNotFoundError:
        return path.parent.resolve() / path.name


def _is_allowed(path: Path) -> bool:
    resolved = _resolve_for_creation(path)
    root = ROOT.resolve()
    resolved_text = str(resolved)
    return (
        resolved == root
        or root in resolved.parents
        or resolved_text.startswith("/tmp/gludd-")
        or resolved_text.startswith("/private/tmp/gludd-")
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <dir>", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    if not _is_allowed(target):
        print(f"ERROR: refusing to create directory outside workspace or /tmp/gludd-*: {target}", file=sys.stderr)
        return 1
    target.mkdir(parents=True, exist_ok=True)
    print(f"Ensured directory: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
