#!/usr/bin/env python3
"""
check_coverage_gaps.py — codified coverage gap audit.

Walks all .py modules in src/general_ludd/ (excluding __init__.py) and checks
for corresponding test files in tests/unit/. Outputs a structured report.

Statuses:
  OK          — test file exists, imports the module, has test_* functions
  UNTESTED    — no test file found
  STUB        — test file exists but has no test_* functions
  NO_IMPORT   — test file exists and has tests but does not import the module

Usage:
    python3 scripts/check_coverage_gaps.py
    python3 scripts/check_coverage_gaps.py --json
    python3 scripts/check_coverage_gaps.py --threshold 3
    python3 scripts/check_coverage_gaps.py --json --threshold 3

Exit codes:
    0  All modules have OK status.
    1  Gaps found (UNTESTED / STUB / NO_IMPORT, or below threshold).
    2  Internal error.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "general_ludd"
TESTS_DIR = PROJECT_ROOT / "tests" / "unit"
DEFAULT_BASELINE = "config/coverage_gaps_baseline.json"


def _load_baseline(baseline_path: Path) -> set[str]:
    if not baseline_path.is_file():
        return set()
    try:
        data = json.loads(baseline_path.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    return set(data.get("allowed_gaps", []))


def _generate_baseline(gap_modules: list[str], baseline_path: Path) -> int:
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"allowed_gaps": sorted(gap_modules)}
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n")
    return len(gap_modules)


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

    # Candidate 1: full path including general_ludd
    stem_full = "_".join(parts)
    candidates.append(TESTS_DIR / f"test_{stem_full}.py")

    # Candidate 2: drop general_ludd_ prefix (most common convention)
    if len(parts) > 1 and parts[0] == "general_ludd":
        stem_no_prefix = "_".join(parts[1:])
        candidates.append(TESTS_DIR / f"test_{stem_no_prefix}.py")

    # Candidate 3: module leaf only.  The repository's long-standing convention
    # uses names such as ``test_tempr_retriever.py`` for nested modules.  Import
    # validation in ``_check_module`` prevents a same-stem test for another
    # package from being accepted accidentally.
    candidates.append(TESTS_DIR / f"test_{parts[-1]}.py")

    return candidates


def _module_path(src_file: Path) -> str:
    rel = src_file.relative_to(PROJECT_ROOT)
    parts = list(rel.parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _test_imports_module(test_file: Path, module_path: str) -> bool:
    try:
        source = test_file.read_text()
    except Exception:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module
            if mod and (mod == module_path or mod.startswith(f"{module_path}.")):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_path or alias.name.startswith(f"{module_path}."):
                    return True
    return False


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
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            count += 1
    return count


def _check_module(src_file: Path) -> dict:
    module_path = _module_path(src_file)
    candidates = _candidate_test_paths(src_file)
    existing = [c for c in candidates if c.is_file()]

    result = {
        "module": str(src_file.relative_to(PROJECT_ROOT)),
        "module_path": module_path,
        "candidate_test_files": [str(c.relative_to(PROJECT_ROOT)) for c in candidates],
    }

    if not existing:
        result["status"] = "UNTESTED"
        result["test_file"] = None
        result["test_count"] = 0
        return result

    inspected = [
        (test_file, _count_test_functions(test_file))
        for test_file in existing
    ]
    for test_file, test_count in inspected:
        if test_count > 0 and _test_imports_module(test_file, module_path):
            result["status"] = "OK"
            result["test_file"] = str(test_file.relative_to(PROJECT_ROOT))
            result["test_count"] = test_count
            return result

    tests_with_functions = [
        (test_file, test_count)
        for test_file, test_count in inspected
        if test_count > 0
    ]
    if not tests_with_functions:
        test_file = existing[0]
        result["status"] = "STUB"
        result["test_file"] = str(test_file.relative_to(PROJECT_ROOT))
        result["test_count"] = 0
        return result

    test_file, test_count = tests_with_functions[0]
    result["status"] = "NO_IMPORT"
    result["test_file"] = str(test_file.relative_to(PROJECT_ROOT))
    result["test_count"] = test_count
    return result


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    generate_baseline = "--generate-baseline" in argv
    threshold = 1
    baseline_path: Path | None = None
    baseline_provided = False

    for arg in argv:
        if arg.startswith("--threshold="):
            try:
                threshold = int(arg.split("=", 1)[1])
            except ValueError:
                print(f"ERROR: invalid threshold: {arg}", file=sys.stderr)
                return 2
        elif arg.startswith("--baseline="):
            raw = arg.split("=", 1)[1]
            baseline_path = PROJECT_ROOT / raw
            baseline_provided = True
        elif arg == "--baseline":
            baseline_path = PROJECT_ROOT / DEFAULT_BASELINE
            baseline_provided = True

    modules = _walk_source_modules()
    results = [_check_module(m) for m in modules]

    ok_results = [r for r in results if r["status"] == "OK"]
    gap_results = [r for r in results if r["status"] != "OK"]

    below_threshold = [r for r in ok_results if r["test_count"] < threshold]

    if generate_baseline:
        if baseline_path is None:
            baseline_path = PROJECT_ROOT / DEFAULT_BASELINE
        gap_modules = [r["module"] for r in gap_results]
        count = _generate_baseline(gap_modules, baseline_path)
        print(f"Baseline written: {baseline_path} ({count} allowed gaps)")
        return 0

    allowed_gaps: set[str] = set()
    new_gap_results: list[dict] = list(gap_results)

    if baseline_provided and baseline_path is not None:
        allowed_gaps = _load_baseline(baseline_path)
        new_gap_results = [r for r in gap_results if r["module"] not in allowed_gaps]

    summary = {
        "total_modules": len(results),
        "ok": len(ok_results),
        "ok_below_threshold": len(below_threshold),
        "untested": sum(1 for r in results if r["status"] == "UNTESTED"),
        "stub": sum(1 for r in results if r["status"] == "STUB"),
        "no_import": sum(1 for r in results if r["status"] == "NO_IMPORT"),
        "threshold": threshold,
        "allowed_gaps": len(allowed_gaps),
        "new_gaps": len(new_gap_results),
    }

    has_gaps = len(new_gap_results) > 0 or len(below_threshold) > 0

    if as_json:
        output = {"summary": summary, "results": results, "exit_code": 1 if has_gaps else 0}
        print(json.dumps(output, indent=2))
        return 1 if has_gaps else 0

    print(f"Coverage Gap Audit — {summary['total_modules']} modules scanned")
    print(f"  OK:            {summary['ok']}")
    print(f"  UNTESTED:      {summary['untested']}")
    print(f"  STUB:          {summary['stub']}")
    print(f"  NO_IMPORT:     {summary['no_import']}")
    if allowed_gaps:
        print(f"  ALLOWED GAPS:  {summary['allowed_gaps']}")
    if summary["new_gaps"] != len(gap_results):
        print(f"  NEW GAPS:      {summary['new_gaps']}")
    if below_threshold:
        print(f"  BELOW THRESHOLD ({threshold}): {len(below_threshold)}")
    print()

    if new_gap_results:
        print(f"--- GAPS ({len(new_gap_results)}) ---")
        for r in new_gap_results:
            path = r["module"]
            status = r["status"]
            test = r.get("test_file") or "(none)"
            print(f"  [{status}] {path}  →  {test}")
        print()

    if below_threshold:
        print(f"--- BELOW TEST THRESHOLD ({threshold}) ---")
        for r in below_threshold:
            print(f"  [{r['test_count']} tests] {r['module']}  →  {r['test_file']}")
        print()

    if has_gaps:
        if allowed_gaps:
            print(
                f"FAIL: {len(new_gap_results)} new gap(s) "
                f"(excluded {len(allowed_gaps)} allowed), "
                f"{len(below_threshold)} below threshold"
            )
        else:
            print(f"FAIL: {len(new_gap_results)} gap(s), {len(below_threshold)} below threshold")
        return 1

    print("PASS: all modules have test coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
