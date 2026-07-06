#!/usr/bin/env python3
"""Static coverage audit: match source files to test imports without running pytest."""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC_DIR = ROOT / "src" / "general_ludd"
TEST_DIR = ROOT / "tests"


def get_source_files() -> list[Path]:
    files = []
    for fpath in SRC_DIR.rglob("*.py"):
        if fpath.name == "__init__.py":
            continue
        files.append(fpath)
    return sorted(files)


def module_name(fpath: Path) -> str:
    rel = fpath.relative_to(SRC_DIR.parent)
    parts = list(rel.parts)
    parts[-1] = parts[-1].replace(".py", "")
    return ".".join(parts)


def find_test_imports(module: str) -> list[str]:
    """Find test files that import from this module."""
    import_patterns = [
        f"from {module} import",
        f"from {module}.",
        f"import {module}",
        f"from {module}.",  # sub-module import
    ]
    found = set()
    for fpath in TEST_DIR.rglob("*.py"):
        try:
            content = fpath.read_text()
        except Exception:
            continue
        for pat in import_patterns:
            if pat in content:
                found.add(str(fpath.relative_to(ROOT)))
    return sorted(found)


def main():
    threshold = int(os.environ.get("THRESHOLD", "85"))
    source_files = get_source_files()

    untested: list[str] = []
    tested_count = 0
    total = len(source_files)

    for fpath in source_files:
        mod = module_name(fpath)
        imports = find_test_imports(mod)
        if imports:
            tested_count += 1
        else:
            untested.append(str(fpath.relative_to(ROOT)))

    pct = round(100.0 * tested_count / total, 1) if total > 0 else 0

    print(f"=== Static Coverage Audit ===")
    print(f"Source files (excl __init__.py): {total}")
    print(f"Files with test imports:         {tested_count}")
    print(f"Files with NO test imports:      {len(untested)}")
    print(f"Test coverage (by import):       {pct}%")
    print(f"Threshold:                       {threshold}%")
    print(f"Meets threshold:                 {'YES' if pct >= threshold else 'NO'}")

    if untested:
        print(f"\n=== Files with NO test imports ({len(untested)}) ===")
        for f in sorted(untested):
            print(f"  {f}")

    sys.exit(0 if pct >= threshold else 1)


if __name__ == "__main__":
    main()
