#!/usr/bin/env python3
"""Find source modules under src/general_ludd/ with no corresponding test file in tests/unit/."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "general_ludd"
TEST = ROOT / "tests" / "unit"

EXCLUDE = {
    "__init__.py", "conftest.py",
}
EXCLUDE_DIRS = {
    "collections",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}

def test_name_for(source_path: Path) -> list[str]:
    """Return possible test file names for a source module."""
    rel = source_path.relative_to(SRC)
    parts = list(rel.parts)
    stem = parts[-1].replace(".py", "")
    # Two naming conventions:
    # 1. test_<stem>.py
    # 2. test_<dir>_<stem>.py (if in subdirectory)
    names = [f"test_{stem}.py"]
    if len(parts) > 1:
        dir_name = parts[-2]
        names.append(f"test_{dir_name}_{stem}.py")
    return names


def find_untested() -> list[tuple[Path, int]]:
    """Return (path, line_count) for untested modules, sorted by lines desc."""
    # Build set of existing test file names
    test_files = set()
    for tf in TEST.rglob("test_*.py"):
        test_files.add(tf.name)

    untested = []
    for py_file in sorted(SRC.rglob("*.py")):
        parts = set(py_file.parts)
        if EXCLUDE_DIRS & parts:
            continue
        if py_file.name in EXCLUDE:
            continue
        # Skip ansible roles/modules
        if "ansible_collections" in py_file.parts:
            continue

        possible_names = test_name_for(py_file)
        if not any(n in test_files for n in possible_names):
            lines = len(py_file.read_text().splitlines())
            untested.append((py_file, lines))

    untested.sort(key=lambda x: -x[1])
    return untested


def main():
    untested = find_untested()
    if not untested:
        print("All modules have corresponding test files!")
        return 0

    print(f"{len(untested)} untested modules found:\n")
    for path, lines in untested:
        rel = path.relative_to(ROOT)
        possible = " or ".join(test_name_for(path))
        print(f"  {rel} — {lines} lines — could be {possible}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
