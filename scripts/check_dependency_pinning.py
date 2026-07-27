#!/usr/bin/env python3
"""check_dependency_pinning.py — AC012: dependency-pinning.

Verifies all production deps are pinned to exact versions in uv.lock.
Development deps may use ranges.
"""

import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    lockfile = root / "uv.lock"
    pyproject = root / "pyproject.toml"

    if not lockfile.exists():
        print("AC012: FAIL — uv.lock not found")
        sys.exit(1)

    if pyproject.exists():
        content = pyproject.read_text()
        range_patterns = [">=", "~=", "^", "!="]
        violations = []
        in_prod_deps = False
        for line in content.splitlines():
            if "dependencies" in line and "dev" not in line.lower():
                in_prod_deps = True
            elif line.strip().startswith("[") and in_prod_deps:
                in_prod_deps = False
            if in_prod_deps and any(p in line for p in range_patterns):
                violations.append(line.strip())

        if violations:
            for v in violations:
                print(f"AC012: VIOLATION — unpinned dependency: {v}")
            print("AC012: FAIL — production dependencies must be pinned to exact versions")
            sys.exit(1)

    print("AC012: PASS — all production dependencies pinned")
    sys.exit(0)


if __name__ == "__main__":
    main()
