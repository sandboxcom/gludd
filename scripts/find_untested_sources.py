"""Find .py files in src/general_ludd/ with no corresponding test in tests/unit/."""

import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "general_ludd")
TESTS = os.path.join(os.path.dirname(__file__), "..", "tests", "unit")


def _strip_src(path):
    """src/general_ludd/foo/bar.py -> foo/bar.py"""
    return os.path.relpath(path, SRC)


def _expected_test_path(src_rel):
    """foo/bar.py -> tests/unit/test_<foo>_<bar|module>.py (two conventions)"""
    parts = src_rel.replace("/", "_").replace(".py", "").split("_")
    candidates = []
    # Convention 1: test_general_ludd_<foo>_<bar>.py
    candidates.append(f"test_general_ludd_{'_'.join(parts)}.py")
    # Convention 2: test_<foo>_<bar>.py (without general_ludd prefix)
    candidates.append(f"test_{'_'.join(parts)}.py")
    # Convention 3: test_<bar>.py (just the module name)
    module_name = parts[-1]
    candidates.append(f"test_{module_name}.py")
    return candidates


def main():
    # Collect source files (excluding __init__.py)
    sources = []
    for root, _dirs, files in os.walk(SRC):
        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                sources.append(os.path.join(root, f))

    # Collect test file names
    test_files = set()
    for root, _dirs, files in os.walk(TESTS):
        for f in files:
            if f.endswith(".py"):
                test_files.add(f)

    # Find untested
    untested = []
    for src in sorted(sources):
        rel = _strip_src(src)
        candidates = _expected_test_path(rel)
        if not any(c in test_files for c in candidates):
            lines = sum(1 for _ in open(src))
            untested.append((src, lines))

    # Sort by line count desc, top 5
    untested.sort(key=lambda x: -x[1])

    if not untested:
        print("All source files have tests.")
        return 0

    for src, lines in untested[:5]:
        print(f"{lines:5d} lines  {src}")

    # Also print full count
    print(f"\nTotal untested: {len(untested)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
