#!/usr/bin/env python3
"""Reclaim only inactive registered worktree virtual environments."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import check_disk_usage
    import clean_ci_shard_scratch
else:
    check_disk_usage = importlib.import_module("scripts.check_disk_usage")
    clean_ci_shard_scratch = importlib.import_module(
        "scripts.clean_ci_shard_scratch"
    )

DEFAULT_WORKTREE_ROOTS = (
    Path("/Users/shawnwilson/gludd/.claude/worktrees"),
    Path("/tmp/gludd-worktrees"),
)

WorktreeRegistryError = check_disk_usage.WorktreeRegistryError
ProcessInspectionError = clean_ci_shard_scratch.ProcessInspectionError

RegisteredWorktreePaths = Callable[[Path], set[Path]]
ActiveProcessPids = Callable[[Path], list[int]]
RemoveTree = Callable[[Path], None]


class CleanupResult(TypedDict):
    """Describe every cleanup action and fail-closed refusal."""

    eligible: list[str]
    removed: list[str]
    skipped: list[str]
    errors: list[str]


def _canonical_registrations(
    worktree_root: Path,
    registered_worktree_paths: RegisteredWorktreePaths,
) -> set[Path]:
    """Return canonical registrations strictly inside one approved namespace."""
    canonical_root = worktree_root.resolve()
    registrations = registered_worktree_paths(worktree_root)
    canonical: set[Path] = set()
    for worktree in registrations:
        candidate = worktree.resolve(strict=True)
        if candidate == canonical_root or not candidate.is_relative_to(canonical_root):
            raise WorktreeRegistryError("registration escaped approved namespace")
        canonical.add(candidate)
    return canonical


def _is_invoking_worktree(invoking_path: Path, worktree: Path) -> bool:
    """Return whether the command was invoked at or inside a worktree."""
    return invoking_path == worktree or invoking_path.is_relative_to(worktree)


def clean_worktree_venvs(
    *,
    worktree_roots: Sequence[Path] = DEFAULT_WORKTREE_ROOTS,
    invoking_path: Path | None = None,
    dry_run: bool = False,
    registered_worktree_paths: RegisteredWorktreePaths = (
        check_disk_usage._registered_worktree_paths
    ),
    active_process_pids: ActiveProcessPids = (
        clean_ci_shard_scratch._active_process_pids
    ),
    remove_tree: RemoveTree = clean_ci_shard_scratch._remove_tree,
) -> CleanupResult:
    """Remove inactive registered peers' venvs while preserving the invoker."""
    result: CleanupResult = {
        "eligible": [],
        "removed": [],
        "skipped": [],
        "errors": [],
    }
    try:
        canonical_invoking = (invoking_path or Path.cwd()).resolve(strict=True)
    except (OSError, RuntimeError):
        result["errors"].append(f"{invoking_path or Path.cwd()}:invoker-unavailable")
        return result

    candidates: list[tuple[Path, Path]] = []
    for worktree_root in worktree_roots:
        try:
            registrations = _canonical_registrations(
                worktree_root, registered_worktree_paths
            )
        except (check_disk_usage.DiskInspectionError, OSError, RuntimeError):
            result["errors"].append(f"{worktree_root}:registry-failed")
            continue
        candidates.extend((worktree_root, worktree) for worktree in registrations)

    for worktree_root, worktree in sorted(candidates, key=lambda item: str(item[1])):
        venv = worktree / ".venv"
        if venv.is_symlink():
            result["errors"].append(f"{venv}:unsafe-venv")
            continue
        if not venv.exists():
            continue
        if not venv.is_dir():
            result["errors"].append(f"{venv}:unsafe-venv")
            continue
        if _is_invoking_worktree(canonical_invoking, worktree):
            result["skipped"].append(f"{venv}:invoking-worktree")
            continue

        try:
            refreshed = _canonical_registrations(
                worktree_root, registered_worktree_paths
            )
        except (check_disk_usage.DiskInspectionError, OSError, RuntimeError):
            result["errors"].append(f"{venv}:registry-failed")
            continue
        if worktree not in refreshed:
            result["errors"].append(f"{venv}:registration-changed")
            continue

        try:
            active_pids = active_process_pids(worktree)
        except ProcessInspectionError:
            result["errors"].append(f"{venv}:process-inspection-failed")
            continue
        if active_pids:
            joined_pids = ",".join(str(pid) for pid in active_pids)
            result["skipped"].append(f"{venv}:active-pids={joined_pids}")
            continue

        if dry_run:
            result["eligible"].append(str(venv))
            continue
        try:
            remove_tree(venv)
        except OSError:
            result["errors"].append(f"{venv}:removal-failed")
            continue
        result["removed"].append(str(venv))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Run inactive-worktree venv cleanup and print bounded action evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = clean_worktree_venvs(dry_run=args.dry_run)

    for path in result["eligible"]:
        print(f"eligible {path}")
    for path in result["removed"]:
        print(f"removed {path}")
    for item in result["skipped"]:
        print(f"skipped {item}")
    for item in result["errors"]:
        print(f"error {item}")
    print(
        "Worktree venv cleanup "
        f"eligible={len(result['eligible'])} "
        f"removed={len(result['removed'])} "
        f"skipped={len(result['skipped'])} "
        f"errors={len(result['errors'])}"
    )
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
