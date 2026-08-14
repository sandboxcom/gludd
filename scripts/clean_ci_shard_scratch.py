#!/usr/bin/env python3
"""Remove stale gludd CI shard scratch directories without touching active runs."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import check_disk_usage
else:
    check_disk_usage = importlib.import_module("scripts.check_disk_usage")

DEFAULT_MIN_AGE_SECONDS = 6 * 60 * 60
PROCESS_INSPECTION_TIMEOUT_SECONDS = 10
PATTERNS = (
    "gludd-ci-shard-*",
    "gludd-gate-unit-*",
    "gludd-unit-shard-*",
    "gludd-audit-e2e-*",
    "gludd-e2e-*",
    "gludd-test-*",
    "gludd-testunit-*",
    "gludd-testspecific-*",
    "gludd-testfiles-*",
)


class ProcessInspectionError(RuntimeError):
    """Signal that active-process inspection could not complete safely."""


def iter_candidates(tmp_root: Path) -> list[Path]:
    """Return deduplicated scratch candidates in deterministic path order."""
    candidates: dict[str, Path] = {}
    for pattern in PATTERNS:
        for path in tmp_root.glob(pattern):
            candidates[str(path)] = path
    return [candidates[key] for key in sorted(candidates)]


def is_stale(path: Path, *, now: float, min_age_seconds: int) -> bool:
    """Return whether a present path is at least the configured age."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    return now - stat.st_mtime >= min_age_seconds


def _remove_tree(path: Path) -> None:
    """Remove stale scratch even when a test left a restrictive mode behind."""
    for root, dirs, files in os.walk(path, topdown=True, onerror=lambda _error: None):
        for name in (*dirs, *files):
            with suppress(OSError):
                os.chmod(Path(root) / name, 0o700)
        with suppress(OSError):
            os.chmod(root, 0o700)
    shutil.rmtree(path)


def _active_process_pids(candidate: Path) -> list[int]:
    """Return processes whose visible command line references a scratch root."""
    canonical_candidate = candidate.resolve()
    candidate_text = os.fsdecode(os.fsencode(canonical_candidate))
    path_pattern = re.compile(
        re.escape(candidate_text) + r"(?=$|[/\s'\"])",
    )
    active_pids: set[int] = set()
    try:
        completed = subprocess.run(
            ["/bin/ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=PROCESS_INSPECTION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessInspectionError("active process inspection failed") from exc
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        if pid != os.getpid() and path_pattern.search(fields[1]):
            active_pids.add(pid)
    return sorted(active_pids)


def clean_ci_shard_scratch(
    *,
    tmp_root: Path = Path("/tmp"),
    min_age_seconds: int = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
    active_process_pids: Callable[[Path], list[int]] = _active_process_pids,
) -> dict[str, list[str]]:
    """Remove stale inactive shard roots and report every removal or refusal."""
    now = time.time()
    removed: list[str] = []
    skipped: list[str] = []
    for path in iter_candidates(tmp_root):
        if not path.exists():
            continue
        if not path.is_dir():
            skipped.append(f"{path}:not-directory")
            continue
        if not is_stale(path, now=now, min_age_seconds=min_age_seconds):
            skipped.append(f"{path}:recent")
            continue
        try:
            active_pids = active_process_pids(path)
        except ProcessInspectionError:
            skipped.append(f"{path}:process-inspection-failed")
            continue
        if active_pids:
            joined_pids = ",".join(str(pid) for pid in active_pids)
            skipped.append(f"{path}:active-pids={joined_pids}")
            continue
        if not dry_run:
            _remove_tree(path)
        removed.append(str(path))
    return {"removed": removed, "skipped": skipped}


def _registration_overlaps(candidate: Path, registered_paths: set[Path]) -> bool:
    """Return whether a candidate intersects any active registration boundary."""
    return any(
        candidate == registered
        or candidate.is_relative_to(registered)
        or registered.is_relative_to(candidate)
        for registered in registered_paths
    )


def clean_orphan_worktree_scratch(
    *,
    worktree_root: Path = check_disk_usage.WORKTREE_ROOT,
    dry_run: bool = True,
    active_process_pids: Callable[[Path], list[int]] = _active_process_pids,
    registered_worktree_paths: Callable[[Path], set[Path]] = (
        check_disk_usage._registered_worktree_paths
    ),
    classify_worktree_children: Callable[
        ..., list[check_disk_usage.ScratchClassification]
    ] = check_disk_usage._classify_worktree_children,
) -> dict[str, list[str]]:
    """Validate or remove only classifier-proven inactive orphan worktree roots."""
    eligible: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []
    try:
        registered_paths = registered_worktree_paths(worktree_root)
        initial_entries = classify_worktree_children(
            worktree_root, registered_paths, observe_exempt=False
        )
        canonical_root = worktree_root.resolve(strict=True)
    except (check_disk_usage.DiskInspectionError, OSError, RuntimeError):
        skipped.append(f"{worktree_root}:classification-failed")
        return {"eligible": eligible, "removed": removed, "skipped": skipped}

    candidates = [
        entry.path for entry in initial_entries if entry.category == "orphan-worktree"
    ]
    for candidate in candidates:
        try:
            canonical_candidate = candidate.resolve(strict=True)
            current_registered = registered_worktree_paths(worktree_root)
            current_entries = classify_worktree_children(
                worktree_root, current_registered, observe_exempt=False
            )
        except (check_disk_usage.DiskInspectionError, OSError, RuntimeError):
            skipped.append(f"{candidate}:classification-failed")
            continue

        if (
            canonical_candidate == canonical_root
            or not canonical_candidate.is_relative_to(canonical_root)
        ):
            skipped.append(f"{candidate}:namespace-conflict")
            continue
        if _registration_overlaps(canonical_candidate, current_registered):
            skipped.append(f"{candidate}:registration-conflict")
            continue

        current_orphans: set[Path] = set()
        try:
            current_orphans = {
                entry.path.resolve(strict=True)
                for entry in current_entries
                if entry.category == "orphan-worktree"
            }
        except (OSError, RuntimeError):
            skipped.append(f"{candidate}:classification-failed")
            continue
        if canonical_candidate not in current_orphans:
            skipped.append(f"{candidate}:classification-changed")
            continue

        try:
            active_pids = active_process_pids(candidate)
        except ProcessInspectionError:
            skipped.append(f"{candidate}:process-inspection-failed")
            continue
        if active_pids:
            joined_pids = ",".join(str(pid) for pid in active_pids)
            skipped.append(f"{candidate}:active-pids={joined_pids}")
            continue
        if dry_run:
            eligible.append(str(candidate))
            continue
        _remove_tree(candidate)
        removed.append(str(candidate))
    return {"eligible": eligible, "removed": removed, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    """Run the bounded stale-shard cleanup command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tmp-root", default="/tmp")
    parser.add_argument("--min-age-seconds", type=int, default=DEFAULT_MIN_AGE_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--worktree-orphans", action="store_true")
    parser.add_argument("--delete-worktree-orphans", action="store_true")
    args = parser.parse_args(argv)

    if args.delete_worktree_orphans and not args.worktree_orphans:
        parser.error("--delete-worktree-orphans requires --worktree-orphans")
    if args.worktree_orphans:
        orphan_result = clean_orphan_worktree_scratch(
            worktree_root=Path(args.tmp_root) / "gludd-worktrees",
            dry_run=not args.delete_worktree_orphans or args.dry_run,
        )
        for path in orphan_result["eligible"]:
            print(f"eligible {path}")
        for path in orphan_result["removed"]:
            print(f"removed {path}")
        for item in orphan_result["skipped"]:
            print(f"skipped {item}")
        print(
            "Orphan worktree scratch cleanup "
            f"eligible={len(orphan_result['eligible'])} "
            f"removed={len(orphan_result['removed'])} "
            f"skipped={len(orphan_result['skipped'])}"
        )
        return 1 if orphan_result["skipped"] else 0

    result = clean_ci_shard_scratch(
        tmp_root=Path(args.tmp_root),
        min_age_seconds=args.min_age_seconds,
        dry_run=args.dry_run,
    )
    for path in result["removed"]:
        print(f"removed {path}")
    for item in result["skipped"]:
        print(f"skipped {item}")

    removed_count = len(result["removed"])
    skipped_count = len(result["skipped"])
    print(
        "Removed stale gludd CI shard scratch directories "
        f"removed={removed_count} skipped={skipped_count}"
    )
    refused = any(
        ":active-pids=" in item or item.endswith(":process-inspection-failed")
        for item in result["skipped"]
    )
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
