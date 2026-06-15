"""Guardrail: assert no git conflict markers survive in tracked text files.

Walks src/, tests/, molecule/, collections/, scripts/, playbooks/, docs/ and
fails with a list of offending file:line combos when any line starts with
'<<<<<<< ', '>>>>>>> ', or equals '=======' (the three git conflict-marker
forms).

This catches the class of bug where a file is syntactically broken by an
unresolved merge but is never imported by the test suite (so collect never
trips on it).
"""

from __future__ import annotations

import os
import pathlib

# Directories to scan — relative to the repo root (parent of tests/).
_SCAN_DIRS = [
    "src",
    "tests",
    "molecule",
    "collections",
    "scripts",
    "playbooks",
    "docs",
]

# Binary-file extensions — skip without opening.
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".whl",
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".a", ".o",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".exe", ".dat",
}


def _repo_root() -> pathlib.Path:
    """Return the repo root (directory that contains tests/)."""
    return pathlib.Path(__file__).parent.parent


def _is_binary_path(path: pathlib.Path) -> bool:
    return path.suffix.lower() in _BINARY_EXTENSIONS


def _has_conflict_marker(line: str) -> bool:
    """Return True if *line* is a git conflict marker line."""
    stripped = line.rstrip("\n")
    if stripped.startswith("<<<<<<< ") or stripped.startswith(">>>>>>> "):
        return True
    return stripped == "======="


def _collect_conflicts() -> list[str]:
    """Scan all configured directories and return a list of 'file:lineno' strings."""
    root = _repo_root()
    violations: list[str] = []

    for scan_dir in _SCAN_DIRS:
        base = root / scan_dir
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # Prune unwanted subtrees in-place.
            dirnames[:] = [
                d for d in dirnames
                if d not in {".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}
            ]
            for filename in filenames:
                filepath = pathlib.Path(dirpath) / filename
                if _is_binary_path(filepath):
                    continue
                try:
                    text = filepath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if _has_conflict_marker(line):
                        rel = filepath.relative_to(root)
                        violations.append(f"{rel}:{lineno}")

    return violations


def test_no_conflict_markers() -> None:
    """No git conflict markers anywhere in tracked text files."""
    violations = _collect_conflicts()
    assert not violations, (
        f"Found {len(violations)} git conflict marker(s) — resolve them before committing:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
