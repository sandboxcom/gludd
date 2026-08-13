#!/usr/bin/env python3
"""Update only the version fields owned by General Ludd."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

_PROJECT_VERSION = re.compile(
    r'^(?P<prefix>\s*version\s*=\s*")[^"]+(?P<suffix>".*)$',
)
_PACKAGE_VERSION = re.compile(
    r'^(?P<prefix>\s*__version__\s*=\s*")[^"]+(?P<suffix>".*)$',
)


def _replace_project_version(text: str, new_version: str) -> str:
    lines = text.splitlines(keepends=True)
    in_project = False
    matches = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        newline = "\n" if line.endswith("\n") else ""
        candidate = line.removesuffix("\n")
        match = _PROJECT_VERSION.fullmatch(candidate)
        if match is None:
            continue
        matches += 1
        lines[index] = (
            f"{match.group('prefix')}{new_version}{match.group('suffix')}"
            f"{newline}"
        )
    if matches != 1:
        raise ValueError(
            "pyproject.toml must contain exactly one [project].version field; "
            f"found {matches}",
        )
    return "".join(lines)


def _replace_package_version(text: str, new_version: str) -> str:
    lines = text.splitlines(keepends=True)
    matches = 0
    for index, line in enumerate(lines):
        newline = "\n" if line.endswith("\n") else ""
        candidate = line.removesuffix("\n")
        match = _PACKAGE_VERSION.fullmatch(candidate)
        if match is None:
            continue
        matches += 1
        lines[index] = (
            f"{match.group('prefix')}{new_version}{match.group('suffix')}"
            f"{newline}"
        )
    if matches != 1:
        raise ValueError(
            "src/general_ludd/__init__.py must contain exactly one "
            f"__version__ field; found {matches}",
        )
    return "".join(lines)


def bump_versions(root: Path, new_version: str) -> tuple[Path, Path]:
    """Validate and atomically prepare both owned version-field updates."""
    try:
        Version(new_version)
    except InvalidVersion as exc:
        raise ValueError(f"invalid PEP 440 version: {new_version!r}") from exc

    pyproject = root / "pyproject.toml"
    package_init = root / "src" / "general_ludd" / "__init__.py"
    original_pyproject = pyproject.read_text()
    original_init = package_init.read_text()

    updated_pyproject = _replace_project_version(
        original_pyproject,
        new_version,
    )
    updated_init = _replace_package_version(original_init, new_version)

    pyproject.write_text(updated_pyproject)
    package_init.write_text(updated_init)
    return pyproject, package_init


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("Usage: bump_version.py NEW_VERSION")
        return 2
    try:
        changed = bump_versions(Path.cwd(), arguments[0])
    except (OSError, ValueError) as exc:
        print(f"Version bump failed: {exc}", file=sys.stderr)
        return 1
    for path in changed:
        print(f"Updated owned version field in {path}")
    print(f"Version bumped to {arguments[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
