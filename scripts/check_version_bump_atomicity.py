#!/usr/bin/env python3
"""check_version_bump_atomicity.py — AC016: version-bump-atomicity.

Verifies all version-bearing files carry the same version after bump.
Files checked: pyproject.toml, __init__.py, CHANGELOG.md, README.md.
"""

import os
import re
import sys
from pathlib import Path


VERSION_FILES = [
    ("pyproject.toml", r'version\s*=\s*"([^"]+)"'),
    ("src/general_ludd/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
    ("CHANGELOG.md", r"##\s+\[?(\d+\.\d+\.\d+(?:-[a-z]+\d*)?)\]?"),
    ("README.md", r"Status as of\s+v?(\d+\.\d+\.\d+(?:-[a-z]+\d*)?)"),
]


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    root = Path(__file__).resolve().parent.parent
    versions = {}

    for relpath, pattern in VERSION_FILES:
        path = root / relpath
        if not path.exists():
            print(f"AC016: WARN — {relpath} not found, skipping")
            continue

        content = path.read_text()
        match = re.search(pattern, content)
        if match:
            versions[relpath] = match.group(1)
        else:
            print(f"AC016: FAIL — cannot find version in {relpath}")
            sys.exit(1)

    unique = set(versions.values())
    if len(unique) > 1:
        print("AC016: FAIL — version mismatch across files:")
        for f, v in versions.items():
            marker = (
                " <-- MISMATCH"
                if list(unique).count(v) == 1 or v != max(unique, key=lambda x: list(versions.values()).count(x))
                else ""
            )
            print(f"  {f}: {v}{marker}")
        sys.exit(1)

    if tag:
        expected = tag.lstrip("v")
        actual = list(unique)[0]
        if actual != expected:
            print(f"AC016: FAIL — file version '{actual}' != tag version '{expected}'")
            sys.exit(1)

    print(f"AC016: PASS — all files at version {list(unique)[0]}")
    sys.exit(0)


if __name__ == "__main__":
    main()
