#!/usr/bin/env python3
"""Enforce tests for every staged production Python change.

Pre-commit guardrail: blocks commits when new/modified source files lack
corresponding test files or when test files are import-only stubs.

Rules:
  1. New .py files in src/general_ludd/ (untracked or staged) MUST have a
     test file staged alongside them.
  2. Modified .py files in src/general_ludd/ must have a corresponding test
     file that imports the module AND contains at least one test_* function.
  3. The corresponding test file MUST also be modified when the source file
     is modified (staged alongside).
  4. Test files that import from a source module must actually USE at least
     one imported name — import-only stubs are blocked.
  5. Allowlist: __init__.py, type stubs, and explicitly listed paths are
     exempt from the test requirement.

Usage:
    python3 scripts/check_tdd_compliance.py

Exit codes:
    0   All checks pass (or no source files changed).
    1   Violations found (commit BLOCKED).
    2   Usage / internal error.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Mutable globals set in main() after parsing --root
PROJECT_ROOT = _DEFAULT_PROJECT_ROOT
SRC_DIR = PROJECT_ROOT / "src" / "general_ludd"
TESTS_DIR = PROJECT_ROOT / "tests"

ALLOWLIST = (
    re.compile(r".*/__pycache__/.*"),
    re.compile(r".*\.pyi$"),
    re.compile(r".*/typing\.py$"),
    re.compile(r".*/type_defs\.py$"),
    re.compile(r".*/protocols\.py$"),
    re.compile(r".*/_types\.py$"),
    re.compile(r".*/__init__\.py$"),
)


def _load_allowlist_config() -> list[re.Pattern[str]]:
    config_path = PROJECT_ROOT / "config" / "tdd_allowlist.yml"
    if not config_path.is_file():
        return []
    if yaml is None:
        print("WARNING: PyYAML not installed, skipping config/tdd_allowlist.yml", file=sys.stderr)
        return []
    try:
        data = yaml.safe_load(config_path.read_text())
    except Exception as exc:
        print(f"WARNING: failed to parse {config_path}: {exc}", file=sys.stderr)
        return []
    entries = data.get("allowlist", []) if isinstance(data, dict) else []
    patterns: list[re.Pattern[str]] = []
    for entry in entries:
        if isinstance(entry, dict) and "path" in entry:
            p_str = entry["path"]
            patterns.append(re.compile(re.escape(str(p_str)) + "$"))
    return patterns


def _git_changed_source_files() -> list[Path]:
    """Return .py files under src/general_ludd/ that are staged or modified."""
    files: list[Path] = []
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.strip().splitlines():
            p = Path(line)
            if p.suffix == ".py" and str(p).startswith("src/general_ludd/"):
                files.append(p)
    except Exception:
        pass
    return files


def _git_all_staged_files() -> set[str]:
    """Return all staged file paths as absolute paths."""
    files: set[str] = set()
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.strip().splitlines():
            if line:
                files.add(str((PROJECT_ROOT / line).resolve()))
    except Exception:
        pass
    return files


_yaml_patterns: list[re.Pattern[str]] | None = None


def _is_allowlisted(path: Path) -> bool:
    global _yaml_patterns
    path_str = str(path)
    for pattern in ALLOWLIST:
        if pattern.search(path_str):
            return True
    if _yaml_patterns is None:
        _yaml_patterns = _load_allowlist_config()
    return any(pattern.search(path_str) for pattern in _yaml_patterns)


def _is_init_in_empty_dir(path: Path) -> bool:
    if path.name != "__init__.py":
        return False
    parent = path.parent
    if not parent.is_dir():
        return False
    try:
        siblings = [p for p in parent.iterdir() if p.is_file() and p.suffix == ".py" and p.name != "__init__.py"]
    except OSError:
        return False
    return len(siblings) == 0


def _module_path(src_file: Path) -> str:
    rel = src_file.relative_to(PROJECT_ROOT) if src_file.is_absolute() else src_file
    parts = list(rel.parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _candidate_test_paths(src_file: Path) -> list[Path]:
    """Map ``src/general_ludd/X.py`` to ``tests/unit/test_X.py`` candidates."""
    rel = src_file.relative_to(SRC_DIR.parent.parent) if src_file.is_absolute() else src_file
    parts = list(rel.parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    stem = "_".join(parts)
    candidates: list[Path] = []
    candidates.append(TESTS_DIR / "unit" / f"test_{stem}.py")
    if len(parts) > 1 and parts[-2] == "connectors":
        candidates.append(TESTS_DIR / "unit" / f"test_connector_{parts[-1]}.py")
    if len(parts) > 1:
        parent = parts[-2]
        leaf = parts[-1]
        candidates.append(TESTS_DIR / "unit" / f"test_{parent}_{leaf}.py")
        candidates.append(TESTS_DIR / "unit" / f"test_{leaf}.py")
    return candidates


def _test_imports_module(test_file: Path, module_path: str) -> bool:
    try:
        source = test_file.read_text()
    except Exception:
        return False
    tree = ast.parse(source)
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


def _test_has_functions(test_file: Path) -> bool:
    try:
        source = test_file.read_text()
    except Exception:
        return False
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def _find_unused_import_names(test_file: Path, module_path: str) -> list[str]:
    """Return names imported from *module_path* that never appear in the test body.

    Covers ``from mod import Name`` (checks for ``Name`` references) and
    ``import mod.sub`` (checks for the top-level scope name, e.g. ``mod``).
    """
    try:
        source = test_file.read_text()
    except Exception:
        return []

    tree = ast.parse(source)

    scoped_names: dict[str, int] = {}  # name_in_scope → import_line
    import_lines: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module
            if mod and (mod == module_path or module_path.startswith(f"{mod}.")):
                for alias in node.names:
                    scoped = alias.asname if alias.asname else alias.name
                    scoped_names[scoped] = node.lineno
                import_lines.add(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_path or alias.name.startswith(f"{module_path}."):
                    scoped = alias.asname if alias.asname else alias.name
                    scoped_names[scoped] = node.lineno
                    import_lines.add(node.lineno)

    if not scoped_names:
        return []

    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.lineno not in import_lines:
            used.add(node.id)
        if (
            isinstance(node, ast.Attribute)
            and node.lineno not in import_lines
            and isinstance(node.value, ast.Name)
        ):
            used.add(node.value.id)

    return [name for name in scoped_names if name not in used]


def _find_valid_test(src_file: Path, module_path: str, staged_set: set[str]) -> tuple[Path | None, str]:
    for candidate in _candidate_test_paths(src_file):
        if not candidate.is_file():
            continue

        if not _test_imports_module(candidate, module_path):
            return candidate, f"test file {candidate} does not import {module_path}"

        if not _test_has_functions(candidate):
            return candidate, f"test file {candidate} has no test_* functions"

        candidate_str = str(candidate)
        if candidate_str not in staged_set:
            return candidate, (
                f"test file {candidate} exists but was NOT modified alongside source file — stage the test changes too"
            )

        unused = _find_unused_import_names(candidate, module_path)
        if unused:
            return candidate, (f"test file {candidate} imports {module_path} but never uses: {', '.join(unused)}")

        return candidate, "ok"

    return None, "no_test_file"


def _parse_root(argv: list[str]) -> Path:
    """Extract --root <path> argument if present; defaults to the script's project."""
    for i, arg in enumerate(argv):
        if arg == "--root" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
    return _DEFAULT_PROJECT_ROOT


def main(argv: list[str]) -> int:
    """Validate staged production files beneath the selected project root."""
    global PROJECT_ROOT, SRC_DIR, TESTS_DIR
    root = _parse_root(argv)
    PROJECT_ROOT = root
    SRC_DIR = root / "src" / "general_ludd"
    TESTS_DIR = root / "tests"

    src_files = _git_changed_source_files()
    if not src_files:
        print("OK: no source files staged for commit")
        return 0

    staged_set = _git_all_staged_files()
    violations: list[str] = []
    checked = 0

    for src_file in src_files:
        if _is_allowlisted(src_file):
            continue
        if _is_init_in_empty_dir(src_file):
            continue

        checked += 1
        module_path = _module_path(src_file)
        test_file, reason = _find_valid_test(src_file, module_path, staged_set)

        if reason == "ok":
            continue

        if test_file is None:
            candidates = _candidate_test_paths(src_file)
            violations.append(
                f"{src_file}: missing test file — "
                f"expected one of: {', '.join(str(c) for c in candidates)} "
                f"(module: {module_path})"
            )
        else:
            violations.append(f"{src_file}: {reason} (module: {module_path})")

    if violations:
        print(f"\nTDD COMPLIANCE VIOLATIONS ({len(violations)}):")
        print("-" * 60)
        for v in violations:
            print(f"  {v}")
        print("-" * 60)
        print(
            "Commit BLOCKED: create a test file that imports the module\n"
            "and contains test_* functions, then stage it alongside the source."
        )
        return 1

    if checked:
        print(f"OK: {checked} source file(s) have valid test coverage")
    else:
        print("OK: no checkable source files staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
