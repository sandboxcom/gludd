#!/usr/bin/env python3
"""Fail when active repository changes have no TASKS.md registration.

The guard intentionally accepts either a file reference in TASKS.md or a valid
task ID in recent commit metadata. This keeps delegated commits traceable while
avoiding a requirement that every task description repeat every changed path.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TASK_ID_RE = re.compile(r"\b(?:[A-Z]{1,3}\d*(?:\.\d+(?:\.\d+)*|[-]\d+))\b")
CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s+(?P<body>.*)$")
IGNORED_PATHS = {
    "TASKS.md",
    ".coverage",
}
IGNORED_PREFIXES = (
    ".git/",
    ".pytest_cache/",
    ".mypy_cache/",
    "__pycache__/",
    ".gate-logs/",
    ".coverage.",
)


def task_ids(tasks_text: str) -> set[str]:
    """Return IDs declared on checkbox task lines only."""
    ids: set[str] = set()
    for line in tasks_text.splitlines():
        match = CHECKBOX_RE.match(line)
        if match:
            ids.update(TASK_ID_RE.findall(match.group("body")))
    return ids


def changed_paths(repo_root: Path) -> list[str]:
    """Return tracked and untracked active paths relative to ``repo_root``."""
    commands = [
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    paths: set[str] = set()
    for command in commands:
        result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, check=True)
        paths.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return sorted(paths)


def recent_commit_messages(repo_root: Path, limit: int = 25) -> list[str]:
    result = subprocess.run(
        ["git", "log", f"-{limit}", "--format=%s"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _ignored(path: str) -> bool:
    return path in IGNORED_PATHS or path.startswith(IGNORED_PREFIXES)


def unregistered_paths(
    tasks_text: str,
    paths: list[str],
    commit_messages: list[str] | None = None,
) -> list[str]:
    """Return active paths lacking both a TASKS path reference and task-ID evidence."""
    active = [path for path in paths if not _ignored(path)]
    if not active:
        return []

    declared_ids = task_ids(tasks_text)
    commit_ids = set()
    for message in commit_messages or []:
        commit_ids.update(TASK_ID_RE.findall(message))
    registered_commit_ids = declared_ids & commit_ids
    registered_lines = [
        line for line in tasks_text.splitlines()
        if CHECKBOX_RE.match(line) and registered_commit_ids.intersection(TASK_ID_RE.findall(line))
    ]

    missing: list[str] = []
    for path in active:
        # A direct path mention is strongest evidence and supports per-file work.
        if path in tasks_text or Path(path).name in tasks_text:
            continue
        # A valid task ID in commit metadata registers the associated change set.
        if any(path in line or Path(path).name in line for line in registered_lines):
            continue
        missing.append(path)
    return missing


def registration_issues(
    tasks_text: str,
    paths: list[str],
    commit_messages: list[str] | None = None,
    delegated_task_ids: list[str] | None = None,
) -> list[str]:
    """Return fail-closed violations for delegation and active work."""
    declared = task_ids(tasks_text)
    issues = [
        f"delegated task ID is not declared: {task_id}"
        for task_id in (delegated_task_ids or [])
        if task_id not in declared
    ]
    issues.extend(f"unregistered path: {path}" for path in unregistered_paths(tasks_text, paths, commit_messages))
    return issues


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    tasks_path = repo_root / "TASKS.md"
    if not tasks_path.exists():
        print("task-registration: ERROR TASKS.md is missing", file=sys.stderr)
        return 1

    paths = changed_paths(repo_root)
    issues = registration_issues(
        tasks_path.read_text(encoding="utf-8"), paths, recent_commit_messages(repo_root)
    )
    if issues:
        print("task-registration: unregistered active work:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        print("Add a checkbox task referencing the path or include its task ID in commit metadata.", file=sys.stderr)
        return 1
    print(f"task-registration: OK ({len(paths)} changed path(s) registered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
