"""Add missing ``import logging`` + ``logger = logging.getLogger(__name__)``
to Python files that reference ``logger`` but lack the import.

Usage: python3 scripts/add_missing_logger_imports.py FILE [FILE ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _end_of_docstring(lines: list[str]) -> int:
    """Return the index of the last line of the opening docstring, or 0."""
    in_docstring = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if in_docstring:
                return i
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                return i
            in_docstring = True
        elif in_docstring:
            continue
        else:
            return i
    return 0


def _import_block_end(lines: list[str], start: int) -> int:
    """Return the index after the last import line, or start."""
    end = start
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if stripped.startswith(("import ", "from ")):
            end = i + 1
        else:
            break
    return end


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "import logging" in text and "logger = logging.getLogger" in text:
        return False

    lines = text.split("\n")

    # 1) Skip past docstring
    insert_at = _end_of_docstring(lines)

    # 2) Skip past `from __future__` line
    if insert_at < len(lines) and "from __future__" in lines[insert_at]:
        insert_at += 1
        # skip blank lines after __future__
        while insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1

    # 3) Skip past existing import block
    insert_at = _import_block_end(lines, insert_at)

    if "import logging" not in text:
        lines.insert(insert_at, "import logging")
        if "logger = logging.getLogger" not in text:
            lines.insert(insert_at + 1, "\nlogger = logging.getLogger(__name__)")
            insert_at += 1
    else:
        if "logger = logging.getLogger" not in text:
            lines.insert(insert_at, "\nlogger = logging.getLogger(__name__)")

    new_text = "\n".join(lines)
    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    files = [Path(a) for a in sys.argv[1:]]
    changed = 0
    for f in files:
        if fix_file(f):
            print(f"Fixed: {f}")
            changed += 1
        else:
            print(f"Skipped (already ok): {f}")
    print(f"\n{changed} file(s) changed.")


if __name__ == "__main__":
    main()
