#!/usr/bin/env python3
"""check_dependency_pinning.py — AC012: dependency-pinning.

Verifies all production deps are pinned to exact versions in uv.lock.
Development deps may use ranges.
"""

import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

RANGE_PATTERNS = [">=", "~=", "^", "!="]


def check_dependency_pinning(content: str) -> tuple[bool, list[str]]:
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


def parse_lockfile_deps(lockfile_content: str) -> dict[str, str]:
    """Return canonical package names and exact versions from a uv lockfile."""
    if not lockfile_content.strip():
        return {}
    try:
        lock = tomllib.loads(lockfile_content)
    except tomllib.TOMLDecodeError:
        return {}

    deps: dict[str, str] = {}
    for package in lock.get("package", []):
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        version = package.get("version")
        if isinstance(name, str) and isinstance(version, str):
            deps[canonicalize_name(name)] = version
    return deps


def _production_requirements(pyproject_content: str) -> list[str]:
    """Extract base and optional runtime requirements using the TOML grammar."""
    try:
        project = tomllib.loads(pyproject_content).get("project", {})
    except tomllib.TOMLDecodeError:
        return []
    if not isinstance(project, dict):
        return []

    requirements = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, dict):
        for entries in optional.values():
            if isinstance(entries, list):
                requirements.extend(entries)
    return [entry for entry in requirements if isinstance(entry, str)]


def find_unpinned_deps(pyproject_content: str, lockfile_deps: Mapping[str, str]) -> list[str]:
    """Find declared runtime requirements missing or unsatisfied in ``uv.lock``."""
    locked = {canonicalize_name(name): version for name, version in lockfile_deps.items()}
    violations: list[str] = []
    for requirement_text in _production_requirements(pyproject_content):
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            violations.append(f"{requirement_text}: invalid requirement")
            continue

        name = canonicalize_name(requirement.name)
        locked_version = locked.get(name)
        if locked_version is None:
            violations.append(f"{name}: not in lockfile")
            continue
        try:
            version = Version(locked_version)
        except InvalidVersion:
            violations.append(f"{name}: invalid locked version {locked_version}")
            continue
        if requirement.specifier and not requirement.specifier.contains(version, prereleases=True):
            violations.append(
                f"{requirement}: locked {locked_version} does not satisfy {requirement.specifier}"
            )
    return violations


def check_lockfile_staleness(lockfile_path: str | Path, pyproject_path: str | Path) -> bool:
    try:
        lock_path = Path(lockfile_path) if not isinstance(lockfile_path, Path) else lockfile_path
        pp_path = Path(pyproject_path) if not isinstance(pyproject_path, Path) else pyproject_path
        return lock_path.stat().st_mtime < pp_path.stat().st_mtime
    except OSError:
        return True


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    lockfile = root / "uv.lock"
    pyproject = root / "pyproject.toml"

    if not lockfile.exists():
        print("AC012: FAIL — uv.lock not found")
        sys.exit(1)

    if not pyproject.exists():
        print("AC012: FAIL — pyproject.toml not found")
        sys.exit(1)

    lockfile_deps = parse_lockfile_deps(lockfile.read_text(encoding="utf-8"))
    violations = find_unpinned_deps(pyproject.read_text(encoding="utf-8"), lockfile_deps)
    if violations:
        for violation in violations:
            print(f"AC012: VIOLATION — {violation}")
        print("AC012: FAIL — production dependencies are not reproducibly resolved")
        sys.exit(1)

    print(f"AC012: PASS — {len(lockfile_deps)} exact locked package versions satisfy project requirements")
    sys.exit(0)


if __name__ == "__main__":
    main()
