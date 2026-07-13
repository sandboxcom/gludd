#!/usr/bin/env python3
"""Pre-commit quality gate for new/modified test files.

Scans tests/unit/ for recently modified test files (via git diff --name-only)
and checks:

  - Unused imports (F401)          - Imports sorted (I001)
  - Unused variables (F841)        - setattr misuse (B010)
  - Test function naming convention (must start with ``test_``)
  - File ends with newline

Uses ruff for the lint checks (the project's configured linter).
Exits 0 if clean, 1 with a specific error listing if issues found.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RULE_CODES = ("F401", "I001", "F841", "B010")


def _test_files_changed() -> list[Path]:
    cmd = ["git", "diff", "--cached", "--name-only"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception:
        return []
    files: list[Path] = []
    for line in result.stdout.strip().splitlines():
        p = Path(line)
        if p.suffix == ".py" and p.parent.match("tests/*"):
            files.append(p)
    return files


def _ruff_errors(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    select = ",".join(RULE_CODES)
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", "--select", select, "--output-format", "concise", *map(str, paths)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 1:
            for line in result.stdout.strip().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("Found ") and not stripped.startswith("[*]"):
                    errors.append(stripped)
    except Exception:
        pass
    return errors


def _naming_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except Exception:
            continue
        for lineno, line in enumerate(lines, start=1):
            if not line.startswith("def "):
                continue
            m = re.match(r"def\s+(\w+)", line)
            if not m:
                continue
            name = m.group(1)
            if name.startswith("test_") or name.startswith("_"):
                continue
            if name in ("setUp", "tearDown"):
                continue
            if _prev_nonblank_line(lines, lineno).startswith("@pytest.fixture"):
                continue
            violations.append(
                f"{path}:{lineno}: non-test function `{name}` — rename to `test_*` or prefix with `_`"
            )
    return violations


def _prev_nonblank_line(lines: list[str], current_lineno: int) -> str:
    for i in range(current_lineno - 2, -1, -1):
        line = lines[i].strip()
        if line.startswith("@"):
            return line
        if line:
            return ""
    return ""


def _newline_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        try:
            text = path.read_text()
        except Exception:
            continue
        if text and not text.endswith("\n"):
            violations.append(f"{path}: file does not end with newline")
    return violations


def main(argv: list[str]) -> int:
    paths = _test_files_changed()
    if not paths:
        print("OK: no test files staged for commit")
        return 0

    print(f"Checking {len(paths)} staged test file(s):")
    for p in paths:
        print(f"  {p}")

    all_errors: list[str] = []
    all_errors.extend(_ruff_errors(paths))
    all_errors.extend(_naming_violations(paths))
    all_errors.extend(_newline_violations(paths))

    if all_errors:
        print(f"\n{len(all_errors)} quality issue(s) found:")
        for err in all_errors:
            print(f"  {err}")
        return 1

    print("OK: no quality issues in staged test files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
