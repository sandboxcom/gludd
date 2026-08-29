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
from collections import defaultdict
from pathlib import Path
from typing import TypeAlias, TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src" / "general_ludd"
TESTS_DIR = PROJECT_ROOT / "tests" / "unit"
DEFAULT_BASELINE = "config/coverage_gaps_baseline.json"

ModuleTests: TypeAlias = dict[str, tuple[Path, ...]]


class CoverageResult(TypedDict):
    module: str
    module_path: str
    candidate_test_files: list[str]
    status: str
    test_file: str | None
    test_count: int


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

    return candidates


def _module_path(src_file: Path) -> str:
    rel = src_file.relative_to(PROJECT_ROOT)
    parts = list(rel.parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def _source_module_paths() -> dict[str, Path]:
    """Return every importable project module, including packages."""
    modules: dict[str, Path] = {}
    for source_file in SRC_DIR.rglob("*.py"):
        relative = source_file.relative_to(SRC_DIR)
        parts = ["general_ludd", *relative.with_suffix("").parts]
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = source_file
    return modules


def _parse_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text())
    except (OSError, SyntaxError, UnicodeError):
        return None


def _absolute_import_from(node: ast.ImportFrom, current_package: str) -> str | None:
    """Resolve an ImportFrom node as Python would inside *current_package*."""
    if node.level == 0:
        return node.module
    package_parts = current_package.split(".")
    remove = node.level - 1
    if remove >= len(package_parts):
        return None
    base = package_parts[: len(package_parts) - remove]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _module_reexports(
    source_modules: dict[str, Path],
) -> dict[tuple[str, str], str]:
    """Map public module attributes to the module that defines them."""
    exports: dict[tuple[str, str], str] = {}
    for module, source_file in source_modules.items():
        tree = _parse_python(source_file)
        if tree is None:
            continue
        current_package = (
            module if source_file.name == "__init__.py" else module.rpartition(".")[0]
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported_from = _absolute_import_from(node, current_package)
            if imported_from is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                public_name = alias.asname or alias.name
                child_module = f"{imported_from}.{alias.name}"
                defining_module = (
                    child_module if child_module in source_modules else imported_from
                )
                if defining_module in source_modules:
                    exports[(module, public_name)] = defining_module
    return exports


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _call_name(node: ast.Call) -> str:
    return _dotted_name(node.func) or ""


def _assigned_expressions(tree: ast.Module) -> dict[str, ast.expr]:
    assignments: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments[node.target.id] = node.value
    return assignments


def _static_path_parts(
    expression: ast.expr,
    assignments: dict[str, ast.expr],
    seen: frozenset[str] = frozenset(),
) -> list[str]:
    """Extract the stable suffix of a statically composed source-file path."""
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return [part for part in expression.value.replace("\\", "/").split("/") if part]
    if isinstance(expression, ast.Name):
        if expression.id in seen or expression.id not in assignments:
            return []
        return _static_path_parts(
            assignments[expression.id], assignments, seen | {expression.id}
        )
    if isinstance(expression, ast.BinOp) and isinstance(
        expression.op, (ast.Add, ast.Div)
    ):
        return [
            *_static_path_parts(expression.left, assignments, seen),
            *_static_path_parts(expression.right, assignments, seen),
        ]
    if isinstance(expression, ast.Call):
        name = _call_name(expression)
        if name.endswith((".join", "Path")):
            parts: list[str] = []
            for argument in expression.args:
                parts.extend(_static_path_parts(argument, assignments, seen))
            return parts
    return []


def _module_from_source_path(parts: list[str], source_modules: dict[str, Path]) -> str | None:
    for index in range(len(parts) - 1):
        if parts[index : index + 2] != ["src", "general_ludd"]:
            continue
        module_parts = parts[index + 1 :]
        if not module_parts[-1].endswith(".py"):
            continue
        module_parts[-1] = module_parts[-1][:-3]
        if module_parts[-1] == "__init__":
            module_parts.pop()
        module = ".".join(module_parts)
        if module in source_modules:
            return module
    return None


def _file_loaded_modules(
    tree: ast.Module,
    source_modules: dict[str, Path],
) -> set[str]:
    assignments = _assigned_expressions(tree)
    modules: set[str] = set()
    path_argument_by_loader = {
        "spec_from_file_location": 1,
        "SourceFileLoader": 1,
        "run_path": 0,
    }
    wrapper_arguments: dict[str, int] = {}
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        parameter_positions = {
            parameter.arg: index for index, parameter in enumerate(function.args.args)
        }
        for child in ast.walk(function):
            if not isinstance(child, ast.Call):
                continue
            loader = _call_name(child).rsplit(".", 1)[-1]
            argument_index = path_argument_by_loader.get(loader)
            if argument_index is None or len(child.args) <= argument_index:
                continue
            path_expression = child.args[argument_index]
            if isinstance(path_expression, ast.Name):
                wrapper_index = parameter_positions.get(path_expression.id)
                if wrapper_index is not None:
                    wrapper_arguments[function.name] = wrapper_index

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        loader = _call_name(node).rsplit(".", 1)[-1]
        argument_index = path_argument_by_loader.get(loader)
        if argument_index is None:
            argument_index = wrapper_arguments.get(loader)
        if argument_index is None or len(node.args) <= argument_index:
            continue
        parts = _static_path_parts(node.args[argument_index], assignments)
        module = _module_from_source_path(parts, source_modules)
        if module is not None:
            modules.add(module)
    return modules


def _modules_imported_by_test(
    tree: ast.Module,
    source_modules: dict[str, Path],
    reexports: dict[tuple[str, str], str],
) -> set[str]:
    imported: set[str] = set()
    local_imports: dict[str, str] = {}

    def add_concrete(module: str | None) -> None:
        if module is None:
            return
        source = source_modules.get(module)
        if source is not None and source.name != "__init__.py":
            imported.add(module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add_concrete(alias.name)
                local_name = alias.asname or alias.name.split(".", 1)[0]
                local_imports[local_name] = alias.name if alias.asname else local_name
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_import_from(node, "")
            add_concrete(module)
            if module is None:
                continue
            for alias in node.names:
                add_concrete(f"{module}.{alias.name}")
                add_concrete(reexports.get((module, alias.name)))
        elif (
            isinstance(node, ast.Call)
            and _call_name(node).endswith("import_module")
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            value = node.args[0].value
            if isinstance(value, str):
                add_concrete(value)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        dotted = _dotted_name(node)
        if dotted is None:
            continue
        root, separator, suffix = dotted.partition(".")
        if not separator or root not in local_imports:
            continue
        expanded = f"{local_imports[root]}.{suffix}"
        add_concrete(expanded)
        parent, _, public_name = expanded.rpartition(".")
        add_concrete(reexports.get((parent, public_name)))

    imported.update(_file_loaded_modules(tree, source_modules))
    return imported


def _build_test_index() -> tuple[ModuleTests, dict[Path, int]]:
    """Parse every test once and index modules by real static imports."""
    source_modules = _source_module_paths()
    reexports = _module_reexports(source_modules)
    tests_by_module: defaultdict[str, set[Path]] = defaultdict(set)
    counts: dict[Path, int] = {}
    for test_file in sorted(TESTS_DIR.rglob("test_*.py")):
        tree = _parse_python(test_file)
        if tree is None:
            continue
        count = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        counts[test_file] = count
        for module in _modules_imported_by_test(tree, source_modules, reexports):
            tests_by_module[module].add(test_file)
    return (
        {module: tuple(sorted(paths)) for module, paths in tests_by_module.items()},
        counts,
    )


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


def _check_module(
    src_file: Path,
    test_index: tuple[ModuleTests, dict[Path, int]] | None = None,
) -> CoverageResult:
    module_path = _module_path(src_file)
    candidates = _candidate_test_paths(src_file)
    existing = [c for c in candidates if c.is_file()]
    tests_by_module, test_counts = test_index or _build_test_index()
    covering = [
        path for path in tests_by_module.get(module_path, ()) if test_counts.get(path, 0) > 0
    ]

    result: CoverageResult = {
        "module": str(src_file.relative_to(PROJECT_ROOT)),
        "module_path": module_path,
        "candidate_test_files": [str(c.relative_to(PROJECT_ROOT)) for c in candidates],
        "status": "UNTESTED",
        "test_file": None,
        "test_count": 0,
    }

    if covering:
        preferred = next((path for path in existing if path in covering), covering[0])
        result["status"] = "OK"
        result["test_file"] = str(preferred.relative_to(PROJECT_ROOT))
        result["test_count"] = sum(test_counts[path] for path in covering)
        return result

    if not existing:
        result["status"] = "UNTESTED"
        result["test_file"] = None
        result["test_count"] = 0
        return result

    test_file = existing[0]
    test_count = test_counts.get(test_file, _count_test_functions(test_file))

    if test_count == 0:
        result["status"] = "STUB"
        result["test_file"] = str(test_file.relative_to(PROJECT_ROOT))
        result["test_count"] = 0
        return result

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
    test_index = _build_test_index()
    results = [_check_module(module, test_index) for module in modules]

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
    new_gap_results: list[CoverageResult] = list(gap_results)

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
