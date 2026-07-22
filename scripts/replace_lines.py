#!/usr/bin/env python3
"""Replace an inclusive line range in an allowed workspace file."""

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
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <file> <start> <end> <new_text_file>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not _is_allowed(path):
        print(f"ERROR: refusing to edit outside workspace or /tmp/gludd-*: {path}", file=sys.stderr)
        return 1
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    if start < 1 or end < start:
        print("ERROR: invalid line range", file=sys.stderr)
        return 1
    new_text = Path(sys.argv[4]).read_text(encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if end > len(lines):
        print(f"ERROR: end line {end} exceeds file length {len(lines)}", file=sys.stderr)
        return 1
    newline = chr(10)
    replacement = new_text.splitlines(keepends=True)
    if new_text and not new_text.endswith(newline):
        replacement[-1] = replacement[-1] + newline
    updated = lines[: start - 1] + replacement + lines[end:]
    path.write_text("".join(updated), encoding="utf-8")
    print(f"Replaced lines {start}-{end} in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
