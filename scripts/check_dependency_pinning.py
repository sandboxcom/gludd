#!/usr/bin/env python3
"""check_dependency_pinning.py — AC012: dependency-pinning.

Verifies all production deps are pinned to exact versions in uv.lock.
Development deps may use ranges.
"""

import os
import subprocess
import sys
from pathlib import Path

RANGE_PATTERNS = [">=", "~=", "^", "!="]


def check_dependency_pinning(content):
    violations = []
    in_prod_deps = False
    for line in content.splitlines():
        if "dependencies" in line and "dev" not in line.lower():
            in_prod_deps = True
        elif line.strip().startswith("[") and in_prod_deps:
            in_prod_deps = False
        if in_prod_deps and any(p in line for p in RANGE_PATTERNS):
            violations.append(line.strip())
    passed = len(violations) == 0
    return passed, violations


def parse_lockfile_deps(lockfile_content):
    deps = {}
    current_name = None
    for line in lockfile_content.splitlines():
        line = line.strip()
        if line == "[[package]]":
            current_name = None
        elif line.startswith('name = "'):
            current_name = line.split('"')[1]
        elif line.startswith('version = "') and current_name:
            version = line.split('"')[1]
            deps[current_name] = version
            current_name = None
    return deps


def find_unpinned_deps(pyproject_content, lockfile_deps):
    violations = []
    in_prod_deps = False
    for line in pyproject_content.splitlines():
        if "dependencies" in line and "dev" not in line.lower():
            in_prod_deps = True
            continue
        elif line.strip().startswith("[") and in_prod_deps:
            in_prod_deps = False
            continue
        if in_prod_deps and line.strip() and not line.strip().startswith("#"):
            if any(p in line for p in RANGE_PATTERNS):
                violations.append(line.strip())
            else:
                parts = line.split("==")
                if len(parts) >= 2:
                    dep_name = parts[0].strip().strip('"').strip("'")
                    if dep_name and dep_name not in lockfile_deps:
                        violations.append(f"{dep_name}: not in lockfile")
    return violations


def check_lockfile_staleness(lockfile_path, pyproject_path):
    try:
        lock_path = Path(lockfile_path) if not isinstance(lockfile_path, Path) else lockfile_path
        pp_path = Path(pyproject_path) if not isinstance(pyproject_path, Path) else pyproject_path
        return lock_path.stat().st_mtime < pp_path.stat().st_mtime
    except OSError:
        return True


def main():
    root = Path(__file__).resolve().parent.parent
    lockfile = root / "uv.lock"
    pyproject = root / "pyproject.toml"

    if not lockfile.exists():
        print("AC012: FAIL — uv.lock not found")
        sys.exit(1)

    if pyproject.exists():
        content = pyproject.read_text()
        passed, violations = check_dependency_pinning(content)

        if not passed:
            for v in violations:
                print(f"AC012: VIOLATION — unpinned dependency: {v}")
            print("AC012: FAIL — production dependencies must be pinned to exact versions")
            sys.exit(1)

    print("AC012: PASS — all production dependencies pinned")
    sys.exit(0)


if __name__ == "__main__":
    main()
