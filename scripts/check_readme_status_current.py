#!/usr/bin/env python3
"""
check_readme_status_current.py

Gate script: asserts that the README "Status as of <version>" line reflects
the version being released.

Usage:
    python3 scripts/check_readme_status_current.py [TAG]

Arguments:
    TAG   Optional.  The release tag (e.g. "v0.1.0-alpha.2" or "0.1.0-alpha.2").
          When omitted the version is read from pyproject.toml [project] version.

Exit codes:
    0   README is current — the "Status as of <version>" line matches the release version.
    1   README is stale, missing the line, or any other failure.

Observable output:
    Always prints what it checked (pyproject version, README line found, comparison result).
"""

from __future__ import annotations

import re
import sys
import os
from pathlib import Path


def _normalize(raw: str) -> str:
    """Strip a leading 'v' and surrounding whitespace for a case-insensitive compare."""
    return raw.strip().lstrip("v").lower()


def _read_pyproject_version(repo_root: Path) -> str:
    """Read version = "..." from pyproject.toml [project] section."""
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject}")
    text = pyproject.read_text(encoding="utf-8")
    # Match the first `version = "..."` line (inside [project])
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("Could not find 'version = \"...\"' in pyproject.toml")
    return m.group(1)


def _find_readme_status_line(repo_root: Path) -> str | None:
    """
    Search README.md for a line containing 'Status as of' followed by a version string.

    Accepted patterns (case-insensitive):
        **Status as of v0.1.0-alpha.2 — 2026-06-18**
        Status as of 0.1.0-alpha.2
        Status as of v0.1.0-alpha.2
    Returns the raw matched version string, or None if the line is absent.
    """
    readme = repo_root / "README.md"
    if not readme.exists():
        raise FileNotFoundError(f"README.md not found at {readme}")
    text = readme.read_text(encoding="utf-8")
    # Look for "Status as of <version>" — version is the first word after "of "
    # that looks like a semver (optional leading v, digits, dots, hyphens, alphanums)
    m = re.search(
        r"[Ss]tatus\s+as\s+of\s+(v?[\w.\-]+)",
        text,
    )
    if not m:
        return None
    return m.group(1)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent

    # --- Determine release version ---
    if len(sys.argv) > 1:
        tag_arg = sys.argv[1].strip()
        release_version = tag_arg
        version_source = f"CLI argument: {tag_arg!r}"
    else:
        try:
            release_version = _read_pyproject_version(repo_root)
            version_source = "pyproject.toml [project] version"
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: could not determine release version: {exc}", file=sys.stderr)
            return 1

    print(f"check-readme-status: release version = {release_version!r}  (source: {version_source})")

    # --- Read README status line ---
    try:
        readme_version_raw = _find_readme_status_line(repo_root)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if readme_version_raw is None:
        print(
            "ERROR: README status table is stale — no 'Status as of <version>' line found in README.md.\n"
            f"  Releasing: {release_version}\n"
            "  Action required: update the Feature & Task Completion Status table "
            "and add a 'Status as of <version>' line before cutting the release.",
            file=sys.stderr,
        )
        return 1

    print(f"check-readme-status: README 'Status as of' version = {readme_version_raw!r}")

    # --- Compare (normalize: strip leading 'v', lowercase) ---
    norm_release = _normalize(release_version)
    norm_readme = _normalize(readme_version_raw)

    if norm_readme == norm_release:
        print(
            f"check-readme-status: OK — README status table is current "
            f"(says {readme_version_raw!r}, releasing {release_version!r})"
        )
        return 0
    else:
        print(
            f"ERROR: README status table is stale: says {readme_version_raw!r}, "
            f"releasing {release_version!r} — "
            "update the Feature & Task Completion Status table and its "
            "'Status as of <version>' line before cutting the release.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
