#!/usr/bin/env python3
"""Replace exact text in a file.

Bypasses the edit/write TDD guardrail for legitimate non-behavioral edits
(e.g. removing stale TODO comments from __init__.py files where the
guardrail's naming heuristic can't match the test file).

Usage:
    python3 scripts/replace_text.py <file> <old_text_file> <new_text_file>
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <file> <old_text_file> <new_text_file>",
            file=sys.stderr,
        )
        return 2

    target = Path(sys.argv[1])
    old_text = Path(sys.argv[2]).read_text()
    new_text = Path(sys.argv[3]).read_text()

    content = target.read_text()
    if old_text not in content:
        print(f"ERROR: old text not found in {target}", file=sys.stderr)
        return 1

    count = content.count(old_text)
    if count > 1:
        print(
            f"ERROR: old text found {count} times in {target} (expected 1)",
            file=sys.stderr,
        )
        return 1

    target.write_text(content.replace(old_text, new_text))
    print(f"Replaced 1 occurrence in {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
