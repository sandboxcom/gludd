#!/usr/bin/env python3
"""Break lines that exceed the ruff line-length limit (120)."""
from __future__ import annotations

import sys
from pathlib import Path

MAX_LEN = 120


def fix_file(path: Path) -> bool:
    changed = False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=False)
    out: list[str] = []
    for i, line in enumerate(lines):
        if len(line) > MAX_LEN and ('"' in line or "'" in line):
            leading = len(line) - len(line.lstrip())
            indent = " " * (leading + 4)
            # Try to split at a comma or `],` that is in the string part
            if "], " in line and line.rstrip().endswith((",")):
                idx = line.index("], ")
                left = line[: idx + 2]
                right = line[idx + 2 :].lstrip()
                out.append(left)
                out.append(indent + right)
                changed = True
                continue
            # Fallback: just keep it (can't safely auto-split)
            out.append(line)
        else:
            out.append(line)
    if changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    files = [
        Path("src/general_ludd/physics/analytical_chemistry.py"),
        Path("src/general_ludd/physics/materials_science.py"),
    ]
    any_changed = False
    for f in files:
        if f.exists():
            if fix_file(f):
                any_changed = True
                print(f"Fixed: {f}")
    return 0 if not any_changed else 0


if __name__ == "__main__":
    sys.exit(main())
