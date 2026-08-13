#!/usr/bin/env python3
"""check_dependency_pinning.py — AC012: dependency-pinning.

Verifies every production requirement has at least one exact, compatible
resolution in uv.lock. Development dependencies may use ranges.
"""

import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

RANGE_PATTERNS = [">=", "~=", "^", "!="]
LockedVersions = str | Sequence[str]


def check_dependency_pinning(content: str) -> tuple[bool, list[str]]:
    violations = []
    in_prod_deps = False
    for line in content.splitlines():
        if "dependencies" in line and "dev" not in line.lower():
            in_prod_deps = True
        elif line.strip().startswith("[") and in_prod_deps:
            in_prod_deps = False
        if in_prod_deps and any(pattern in line for pattern in RANGE_PATTERNS):
            violations.append(line.strip())
    passed = len(violations) == 0
    return passed, violations


def parse_lockfile_deps(lockfile_content: str) -> dict[str, tuple[str, ...]]:
    """Return every exact version per canonical package name from uv.lock."""

    if not lockfile_content.strip():
        return {}
    try:
        lock = tomllib.loads(lockfile_content)
    except tomllib.TOMLDecodeError:
        return {}

    collected: dict[str, list[str]] = {}
    for package in lock.get("package", []):
        if not isinstance(package, dict):
            continue
        name = package.get("name")
        version = package.get("version")
        if isinstance(name, str) and isinstance(version, str):
            versions = collected.setdefault(canonicalize_name(name), [])
            if version not in versions:
                versions.append(version)
    return {name: tuple(versions) for name, versions in collected.items()}


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


def _version_candidates(value: LockedVersions) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def find_unpinned_deps(
    pyproject_content: str,
    lockfile_deps: Mapping[str, LockedVersions],
) -> list[str]:
    """Find runtime requirements missing or unsatisfied in uv.lock.

    A uv universal lock may contain multiple versions of one package for
    disjoint Python/platform markers. Each declared requirement must match at
    least one exact locked version; uv lock --check validates the marker graph.
    """

    locked = {
        canonicalize_name(name): _version_candidates(versions)
        for name, versions in lockfile_deps.items()
    }
    violations: list[str] = []
    for requirement_text in _production_requirements(pyproject_content):
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement:
            violations.append(f"{requirement_text}: invalid requirement")
            continue

        name = canonicalize_name(requirement.name)
        candidates = locked.get(name)
        if not candidates:
            violations.append(f"{name}: not in lockfile")
            continue

        valid_versions: list[Version] = []
        invalid_candidates: list[str] = []
        for candidate in candidates:
            try:
                valid_versions.append(Version(candidate))
            except InvalidVersion:
                invalid_candidates.append(candidate)
        if invalid_candidates:
            joined = ", ".join(invalid_candidates)
            violations.append(f"{name}: invalid locked version {joined}")
            continue

        if requirement.specifier and not any(
            requirement.specifier.contains(version, prereleases=True)
            for version in valid_versions
        ):
            if len(candidates) == 1:
                detail = f"locked {candidates[0]} does not satisfy"
            else:
                detail = f"locked versions {', '.join(candidates)} do not satisfy"
            violations.append(f"{requirement}: {detail} {requirement.specifier}")
    return violations


def check_lockfile_staleness(
    lockfile_path: str | Path,
    pyproject_path: str | Path,
) -> bool:
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
    violations = find_unpinned_deps(
        pyproject.read_text(encoding="utf-8"),
        lockfile_deps,
    )
    if violations:
        for violation in violations:
            print(f"AC012: VIOLATION — {violation}")
        print("AC012: FAIL — production dependencies are not reproducibly resolved")
        sys.exit(1)

    version_count = sum(len(versions) for versions in lockfile_deps.values())
    print(
        f"AC012: PASS — {version_count} exact locked package versions "
        "satisfy project requirements"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
