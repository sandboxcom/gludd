#!/usr/bin/env python3
"""Bind the active release runbook to project metadata and native Make owners."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from scripts.makefile_layout import MakefileLayoutError, compose_makefile
else:
    from makefile_layout import MakefileLayoutError, compose_makefile

MAKE_COMMAND = re.compile(r"(?m)(?:^|`)[ \t]*make[ \t]+([A-Za-z_][A-Za-z0-9_.-]*)\b")
RELEASE_TARGET = re.compile(r"(?m)^Release target:[ \t]*`v([^`\r\n]+)`\.?[ \t]*$")


def extract_make_targets(content: str) -> set[str]:
    """Extract inline and line-start Make commands, including their arguments."""
    return set(MAKE_COMMAND.findall(content))


def parse_runbook_date(content: str) -> str | None:
    """Return a historical last-updated field when the document provides one."""
    match = re.search(r"Last\s+updated[:.]?\s*(.+)", content, re.IGNORECASE)
    return match.group(1).strip() if match else None


def check_version_in_runbook(content: str, version: str) -> bool:
    """Check a generic version mention without granting active-release authority."""
    return version in content if version else True


def check_targets_exist(targets: list[str], makefile_content: str) -> list[str]:
    """Return referenced owners absent from the composed native Make sources."""
    return sorted(
        target for target in targets
        if not re.search(rf"^{re.escape(target)}:", makefile_content, re.MULTILINE)
    )


def find_missing_targets(targets: set[str], makefile_text: str) -> list[str]:
    """Adapt the set-based historical API to the deterministic owner check."""
    return check_targets_exist(list(targets), makefile_text)


def _project_version(root: Path) -> str:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project: object = payload.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml must contain a project table")
    version: object = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("pyproject.toml project.version must be a nonempty string")
    return version


def main() -> None:
    """Fail closed unless the active version and every documented owner agree."""
    root = Path(__file__).resolve().parent.parent
    try:
        version = _project_version(root)
        tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")
        if tag and tag.removeprefix("v") != version:
            raise ValueError(f"requested tag differs from project version {version}")
        content = (root / "docs/RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
        if RELEASE_TARGET.findall(content) != [version]:
            raise ValueError(f"declare exactly one Release target: `v{version}`.")
        makefile_text = compose_makefile(root / "Makefile")
        missing_targets = find_missing_targets(extract_make_targets(content), makefile_text)
        if missing_targets:
            raise ValueError("runbook references nonexistent targets: " + ", ".join(missing_targets))
    except (OSError, ValueError, MakefileLayoutError) as exc:
        print(f"AC013: FAIL - {exc}")
        raise SystemExit(1) from exc
    print(f"AC013: PASS - active v{version}; runbook owners resolve through native Make sources")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
