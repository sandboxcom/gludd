#!/usr/bin/env python3
"""check_version_bump_atomicity.py — AC016: version-bump-atomicity.

Verifies all version-bearing files carry the same version after bump.
Files checked: pyproject.toml, __init__.py, CHANGELOG.md, README.md.
"""

import os
import re
import sys
from collections import Counter
from pathlib import Path

_VERSION_TOKEN = r"(\d+(?:\.\d+)+(?:[-+._]?[0-9A-Za-z]+)*)"

VERSION_FILES = [
    ("pyproject.toml", r'version\s*=\s*"([^"]+)"'),
    ("src/general_ludd/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
    ("CHANGELOG.md", rf"##\s+\[?{_VERSION_TOKEN}\]?"),
    ("README.md", rf"Status as of\s+v?{_VERSION_TOKEN}"),
]


def extract_version_from_toml(content: str) -> str | None:
    match = re.search(r'version\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


def extract_version_from_init(content: str) -> str | None:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


def extract_version_from_changelog(content: str) -> str | None:
    match = re.search(dict(VERSION_FILES)["CHANGELOG.md"], content)
    return match.group(1) if match else None


def extract_version_from_readme(content: str) -> str | None:
    match = re.search(dict(VERSION_FILES)["README.md"], content)
    return match.group(1) if match else None


def check_atomicity(versions_dict: dict[str, str | None]) -> tuple[bool, list[str]]:
    valid = {k: v for k, v in versions_dict.items() if v is not None}
    if not valid:
        return False, ["No versions found in any files"]
    unique = set(valid.values())
    if len(unique) > 1:
        return False, [f"Version mismatch: {sorted(unique)}"]
    return True, [next(iter(unique))]


def extract_versions(root: Path) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for relpath, pattern in VERSION_FILES:
        path = root / relpath
        if not path.exists():
            versions[relpath] = None
            continue
        content = path.read_text()
        match = re.search(pattern, content)
        versions[relpath] = match.group(1) if match else None
    return versions


def check_version_consistency(versions: dict[str, str | None], expected_tag: str = "") -> list[str]:
    errors: list[str] = []
    valid = {k: v for k, v in versions.items() if v is not None}
    if not valid:
        errors.append("No versions found in any files")
        return errors
    unique = set(valid.values())
    if len(unique) > 1:
        errors.append(f"Version mismatch across files: {sorted(unique)}")
    if expected_tag:
        expected = expected_tag.lstrip("v")
        actual = sorted(unique)[0]
        if actual != expected:
            errors.append(f"File version '{actual}' != tag version '{expected}'")
    return errors


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
    root = Path(__file__).resolve().parent.parent
    versions = extract_versions(root)
    for relpath, version in list(versions.items()):
        if version is None and not (root / relpath).exists():
            print(f"AC016: WARN — {relpath} not found, skipping")
            del versions[relpath]
            continue
        if version is None:
            print(f"AC016: FAIL — cannot find version in {relpath}")
            sys.exit(1)

    unique = {version for version in versions.values() if version is not None}
    if not unique:
        print("AC016: FAIL — no version-bearing files found")
        sys.exit(1)
    if len(unique) > 1:
        print("AC016: FAIL — version mismatch across files:")
        valid_versions = {path: version for path, version in versions.items() if version is not None}
        majority_version = Counter(valid_versions.values()).most_common(1)[0][0]
        for path, version in valid_versions.items():
            marker = " <-- MISMATCH" if version != majority_version else ""
            print(f"  {path}: {version}{marker}")
        sys.exit(1)

    if tag:
        expected = tag.lstrip("v")
        actual = next(iter(unique))
        if actual != expected:
            print(f"AC016: FAIL — file version '{actual}' != tag version '{expected}'")
            sys.exit(1)

    print(f"AC016: PASS — all files at version {next(iter(unique))}")
    sys.exit(0)


if __name__ == "__main__":
    main()
