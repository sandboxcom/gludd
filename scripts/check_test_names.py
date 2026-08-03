"""check_test_names.py — AA094 enforcement.

Check that test function names describe expected behavior, not old bugs.
Report test names containing bug-tracking keywords (e.g., "despite_env",
"bypass_guard") that may be misleading after a fix.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"

BUGGY_KEYWORDS = [
    "despite_env_disabled",
    "bypass_guard",
    "workaround",
    "hack_",
    "_after_crash",
    "_after_incident",
    "regression_",
    "hotfix_",
    "_recovery_workaround",
]

SUMMARY_KEYWORDS = [
    "test_",
]


def _find_buggy_names() -> list[tuple[Path, int, str]]:
    results: list[tuple[Path, int, str]] = []
    for py_file in TESTS_DIR.rglob("test_*.py"):
        for i, line in enumerate(py_file.read_text().split("\n"), 1):
            m = re.match(r"^\s*def\s+(\w+)", line)
            if not m:
                continue
            name = m.group(1).lower()
            for kw in BUGGY_KEYWORDS:
                if kw in name:
                    results.append((py_file, i, m.group(1)))
                    break
    return results


def main() -> int:
    bad = _find_buggy_names()
    if not bad:
        print("No buggy test names detected.")
        return 0
    print(f"{len(bad)} test(s) with potentially misleading names:")
    for path, line, name in bad:
        print(f"  {path}:{line}: {name}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
