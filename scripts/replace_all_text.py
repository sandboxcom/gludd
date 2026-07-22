#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print("Usage: replace_all_text.py FILE OLD_FILE NEW_FILE", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    old = Path(sys.argv[2]).read_text(encoding="utf-8")
    new = Path(sys.argv[3]).read_text(encoding="utf-8")
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        print(f"ERROR: old text not found in {target}", file=sys.stderr)
        return 1
    target.write_text(text.replace(old, new), encoding="utf-8")
    print(f"Replaced {count} occurrence(s) in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
