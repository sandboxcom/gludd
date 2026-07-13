#!/usr/bin/env python3
"""
check_coverage_missing.py — structural test coverage gap checker.

Scans src/general_ludd/ for all Python modules (excluding __init__.py, __pycache__,
and .pyi stubs) and cross-references tests/unit/ for corresponding test files.

Reports:
  - UNTESTED: module has no corresponding test file
  - STUB:     test file exists but has 0 test_* functions

Exit 0: all modules have a test file with at least 1 test function.
Exit 1: gaps found.
Exit 2: internal error.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "general_ludd"
TESTS_DIR = PROJECT_ROOT / "tests" / "unit"


def _walk_source_modules() -> list[Path]:
    modules: list[Path] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        if "__pycache__" in py_file.parts:
            continue
        if py_file.suffix == ".pyi":
            continue
        modules.append(py_file)
    return modules


def _candidate_test_paths(src_file: Path) -> list[Path]:
    rel = src_file.relative_to(PROJECT_ROOT)
    parts = list(rel.parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    candidates: list[Path] = []

    # full path including general_ludd_ prefix
    stem_full = "_".join(parts)
    candidates.append(TESTS_DIR / f"test_{stem_full}.py")

    # drop general_ludd_ prefix (most common convention)
    if len(parts) > 1 and parts[0] == "general_ludd":
        stem_no_prefix = "_".join(parts[1:])
        candidates.append(TESTS_DIR / f"test_{stem_no_prefix}.py")

    return candidates


def _count_test_functions(test_file: Path) -> int:
    try:
        source = test_file.read_text()
    except Exception:
        return 0
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            count += 1
    return count


def main() -> int:
    modules = _walk_source_modules()
    untested: list[Path] = []
    stubs: list[Path] = []

    for src_file in modules:
        candidates = _candidate_test_paths(src_file)
        existing = [c for c in candidates if c.is_file()]

        if not existing:
            untested.append(src_file)
            continue

        test_file = existing[0]
        test_count = _count_test_functions(test_file)
        if test_count == 0:
            stubs.append((src_file, test_file))

    total = len(modules)
    has_gaps = bool(untested) or bool(stubs)

    print(f"Coverage Missing Audit — {total} modules scanned")
    print()

    if untested:
        print(f"--- UNTESTED ({len(untested)}) ---")
        for m in untested:
            rel = m.relative_to(PROJECT_ROOT)
            print(f"  [UNTESTED] {rel}")
        print()

    if stubs:
        print(f"--- STUB ({len(stubs)}) ---")
        for src_file, test_file in stubs:
            rel_src = src_file.relative_to(PROJECT_ROOT)
            rel_tst = test_file.relative_to(PROJECT_ROOT)
            print(f"  [STUB] {rel_src}  ->  {rel_tst} (0 test functions)")
        print()

    if has_gaps:
        print(f"FAIL: {len(untested)} untested, {len(stubs)} stub(s)")
        return 1

    print(f"PASS: all {total} modules have a test file with >=1 test function")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
