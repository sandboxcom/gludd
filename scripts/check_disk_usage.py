#!/usr/bin/env python3
"""Pre-commit disk check for generated gludd scratch and root disk usage.

Exit 0 = ok, exit 1 = over threshold.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

GLUDD_TMP_LIMIT_MB = 100
DISK_USAGE_PCT_LIMIT = 90
TMP_ROOT = Path("/tmp")
WORKTREE_ROOT = TMP_ROOT / "gludd-worktrees"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKTREE_GENERATED_DIRS = (".pytest_cache", ".mypy_cache", ".ruff_cache")
WORKTREE_LIST_TIMEOUT_SECONDS = 10
CLASSIFICATION_ENTRY_LIMIT = 40
FAILURE_DETAIL_LIMIT = 3


class DiskInspectionError(RuntimeError):
    """Signal that a required disk-safety input could not be inspected."""


class WorktreeRegistryError(DiskInspectionError):
    """Signal that Git's authoritative worktree registry was unavailable."""


class ScratchClassification(NamedTuple):
    """Describe observed and budgeted bytes for one namespaced scratch root."""

    path: Path
    category: str
    observed_size_bytes: int | None
    counted_size_bytes: int


def _file_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError as exc:
        raise DiskInspectionError(f"scratch inspection failed: {path}") from exc


def _tree_size_bytes(root: Path) -> int:
    try:
        if not root.exists():
            return 0
        if root.is_file():
            return _file_size_bytes(root)

        total = 0
        iterator = root.rglob("*")
        for path in iterator:
            if path.is_file():
                total += _file_size_bytes(path)
        return total
    except (OSError, RuntimeError) as exc:
        raise DiskInspectionError(f"scratch inspection failed: {root}") from exc


def _parse_registered_worktrees(payload: bytes, worktree_root: Path) -> set[Path]:
    """Parse active namespace descendants from Git's NUL porcelain format."""
    if not payload or not payload.endswith(b"\0\0"):
        raise WorktreeRegistryError("malformed git worktree list output")

    registered: set[Path] = set()
    canonical_root = worktree_root.resolve()
    for raw_record in payload[:-2].split(b"\0\0"):
        fields = raw_record.split(b"\0")
        if not fields or not fields[0].startswith(b"worktree "):
            raise WorktreeRegistryError("malformed git worktree list output")
        raw_path = fields[0].removeprefix(b"worktree ")
        if not raw_path:
            raise WorktreeRegistryError("malformed git worktree list output")
        if any(field == b"prunable" or field.startswith(b"prunable ") for field in fields[1:]):
            continue

        candidate = Path(os.fsdecode(raw_path))
        if not candidate.is_absolute():
            raise WorktreeRegistryError("git worktree list returned a relative path")
        try:
            canonical_candidate = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if (
            canonical_candidate != canonical_root
            and canonical_candidate.is_relative_to(canonical_root)
            and canonical_candidate.is_dir()
        ):
            registered.add(canonical_candidate)
    return registered


def _registered_worktree_paths(worktree_root: Path) -> set[Path]:
    """Return active worktrees registered by Git below the scratch namespace."""
    command = ["git", "worktree", "list", "--porcelain", "-z"]
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            timeout=WORKTREE_LIST_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeRegistryError("git worktree list failed") from exc
    return _parse_registered_worktrees(completed.stdout, worktree_root)


def _classify_worktree_children(
    worktree_root: Path,
    registered_worktrees: set[Path],
    *,
    observe_exempt: bool,
) -> list[ScratchClassification]:
    """Partition namespace children around registered descendant worktrees."""
    if not worktree_root.exists():
        return []
    if not worktree_root.is_dir():
        size_bytes = _tree_size_bytes(worktree_root)
        return [
            ScratchClassification(
                worktree_root, "orphan-worktree-root", size_bytes, size_bytes
            )
        ]

    try:
        worktrees = sorted(worktree_root.iterdir(), key=lambda path: os.fsencode(path.name))
    except OSError as exc:
        raise DiskInspectionError(
            f"scratch inspection failed: {worktree_root}"
        ) from exc

    def classify_path(worktree: Path, ancestor_paths: frozenset[Path]) -> None:
        try:
            canonical_worktree = worktree.resolve(strict=True)
        except (OSError, RuntimeError):
            canonical_worktree = None
        if canonical_worktree in registered_worktrees and worktree.is_dir():
            counted_bytes = sum(
                _tree_size_bytes(worktree / generated_name)
                for generated_name in WORKTREE_GENERATED_DIRS
            )
            observed_bytes = _tree_size_bytes(worktree) if observe_exempt else None
            classifications.append(
                ScratchClassification(
                    worktree,
                    "registered-worktree-generated",
                    observed_bytes,
                    counted_bytes,
                )
            )
            return

        registered_descends_from_worktree = (
            canonical_worktree is not None
            and worktree.is_dir()
            and any(
                registered != canonical_worktree
                and registered.is_relative_to(canonical_worktree)
                for registered in registered_worktrees
            )
        )
        if registered_descends_from_worktree:
            if canonical_worktree is None:
                raise DiskInspectionError(f"scratch inspection failed: {worktree}")
            if canonical_worktree in ancestor_paths:
                raise DiskInspectionError(
                    f"scratch inspection failed: recursive namespace link {worktree}"
                )
            try:
                children = sorted(
                    worktree.iterdir(), key=lambda path: os.fsencode(path.name)
                )
            except OSError as exc:
                raise DiskInspectionError(
                    f"scratch inspection failed: {worktree}"
                ) from exc
            next_ancestors = ancestor_paths | {canonical_worktree}
            for child in children:
                classify_path(child, next_ancestors)
            return

        size_bytes = _tree_size_bytes(worktree)
        classifications.append(
            ScratchClassification(worktree, "orphan-worktree", size_bytes, size_bytes)
        )

    classifications: list[ScratchClassification] = []
    for worktree in worktrees:
        classify_path(worktree, frozenset())
    return classifications


def _worktree_generated_size_bytes(
    worktree_root: Path, registered_worktrees: set[Path]
) -> int:
    """Count active caches and every byte in unregistered worktree roots."""
    return sum(
        entry.counted_size_bytes
        for entry in _classify_worktree_children(
            worktree_root, registered_worktrees, observe_exempt=False
        )
    )


def _classify_gludd_tmp(
    tmp_root: Path = TMP_ROOT,
    worktree_root: Path = WORKTREE_ROOT,
    registered_worktrees: set[Path] | None = None,
    *,
    observe_exempt: bool = False,
) -> list[ScratchClassification]:
    """Classify every `/tmp/gludd-*` root with exact counted byte totals."""
    classifications: list[ScratchClassification] = []
    canonical_worktree_root = worktree_root.resolve()
    for entry in sorted(tmp_root.glob("gludd-*"), key=lambda path: os.fsencode(path.name)):
        if entry.resolve() == canonical_worktree_root:
            active_worktrees = (
                _registered_worktree_paths(worktree_root)
                if registered_worktrees is None
                else registered_worktrees
            )
            classifications.extend(
                _classify_worktree_children(
                    worktree_root,
                    active_worktrees,
                    observe_exempt=observe_exempt,
                )
            )
            continue
        size_bytes = _tree_size_bytes(entry)
        classifications.append(
            ScratchClassification(entry, "generated-scratch", size_bytes, size_bytes)
        )
    return classifications


def _gludd_tmp_size_mb(
    tmp_root: Path = TMP_ROOT,
    worktree_root: Path = WORKTREE_ROOT,
    registered_worktrees: set[Path] | None = None,
) -> float:
    """Return generated /tmp/gludd-* scratch size in MB.

    Active git worktree source and worktree-local .venv directories under
    /tmp/gludd-worktrees are intentionally excluded. Small generated tool
    caches inside those worktrees still count against the scratch limit.
    """
    size_mb, _ = _gludd_tmp_inspection(
        tmp_root=tmp_root,
        worktree_root=worktree_root,
        registered_worktrees=registered_worktrees,
    )
    return size_mb


def _gludd_tmp_inspection(
    tmp_root: Path = TMP_ROOT,
    worktree_root: Path = WORKTREE_ROOT,
    registered_worktrees: set[Path] | None = None,
) -> tuple[float, list[ScratchClassification]]:
    """Return counted scratch megabytes together with its root classifications."""
    classifications = _classify_gludd_tmp(
        tmp_root=tmp_root,
        worktree_root=worktree_root,
        registered_worktrees=registered_worktrees,
    )
    size_mb = sum(entry.counted_size_bytes for entry in classifications) / (
        1024 * 1024
    )
    return size_mb, classifications


def _largest_counted_roots(classifications: list[ScratchClassification]) -> str:
    """Format a bounded, injection-safe list of the largest counted roots."""
    ranked = sorted(
        (entry for entry in classifications if entry.counted_size_bytes > 0),
        key=lambda entry: (-entry.counted_size_bytes, os.fsencode(entry.path)),
    )
    return ", ".join(
        f"{entry.category} "
        f"{json.dumps(os.fsdecode(os.fsencode(entry.path)))}="
        f"{entry.counted_size_bytes / (1024 * 1024):.1f} MB"
        for entry in ranked[:FAILURE_DETAIL_LIMIT]
    )


def _print_classification_report() -> int:
    """Print a bounded JSON-lines account of observed and counted scratch roots."""
    try:
        classifications = _classify_gludd_tmp(observe_exempt=True)
    except DiskInspectionError as exc:
        print(f"CLASSIFICATION FAIL: {exc}", file=sys.stderr)
        return 1

    ranked = sorted(
        classifications,
        key=lambda entry: (
            -entry.counted_size_bytes,
            -(entry.observed_size_bytes or 0),
            os.fsencode(entry.path),
        ),
    )
    for entry in ranked[:CLASSIFICATION_ENTRY_LIMIT]:
        print(
            json.dumps(
                {
                    "category": entry.category,
                    "counted_bytes": entry.counted_size_bytes,
                    "counted_mb": round(entry.counted_size_bytes / (1024 * 1024), 3),
                    "observed_bytes": entry.observed_size_bytes,
                    "observed_mb": (
                        round(entry.observed_size_bytes / (1024 * 1024), 3)
                        if entry.observed_size_bytes is not None
                        else None
                    ),
                    "path": os.fsdecode(os.fsencode(entry.path)),
                },
                sort_keys=True,
            )
        )
    print(
        json.dumps(
            {
                "summary": {
                    "counted_bytes": sum(
                        entry.counted_size_bytes for entry in classifications
                    ),
                    "omitted_entries": max(
                        0, len(classifications) - CLASSIFICATION_ENTRY_LIMIT
                    ),
                    "total_entries": len(classifications),
                }
            },
            sort_keys=True,
        )
    )
    return 0


def _disk_usage_pct(target: Path = REPOSITORY_ROOT) -> float:
    """Return repository-volume disk usage percentage, from 0 to 100."""
    try:
        out = subprocess.check_output(["df", "-Pk", str(target)], text=True, timeout=10)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        raise DiskInspectionError("disk usage inspection failed") from exc
    parts = out.strip().split("\n")[-1].split()
    if len(parts) < 5:
        raise DiskInspectionError("disk usage inspection failed: malformed df output")
    try:
        percentage = float(parts[4].rstrip("%"))
    except ValueError as exc:
        raise DiskInspectionError("disk usage inspection failed: malformed percentage") from exc
    if not 0.0 <= percentage <= 100.0:
        raise DiskInspectionError("disk usage inspection failed: percentage out of range")
    return percentage


def main() -> int:
    """Check scratch and repository-volume usage against fixed safety limits."""
    failures: list[str] = []
    try:
        disk_pct = _disk_usage_pct()
    except DiskInspectionError as exc:
        disk_pct = None
        failures.append(str(exc))

    try:
        tmp_mb, classifications = _gludd_tmp_inspection()
    except WorktreeRegistryError as exc:
        tmp_mb = None
        classifications = []
        failures.append(f"worktree registry unavailable: {exc}")
    except DiskInspectionError as exc:
        tmp_mb = None
        classifications = []
        failures.append(str(exc))
    if tmp_mb is not None and tmp_mb > GLUDD_TMP_LIMIT_MB:
        largest_roots = _largest_counted_roots(classifications)
        failures.append(
            f"generated /tmp/gludd-* scratch {tmp_mb:.1f} MB > "
            f"{GLUDD_TMP_LIMIT_MB} MB limit; largest counted roots: {largest_roots}"
        )
    if disk_pct is not None and disk_pct > DISK_USAGE_PCT_LIMIT:
        failures.append(f"disk usage {disk_pct:.1f}% > {DISK_USAGE_PCT_LIMIT}% limit")

    if not failures:
        assert tmp_mb is not None
        assert disk_pct is not None
        print(
            f"disk ok: generated /tmp/gludd-* scratch = {tmp_mb:.1f} MB "
            f"(<= {GLUDD_TMP_LIMIT_MB}), disk = {disk_pct:.1f}% "
            f"(<= {DISK_USAGE_PCT_LIMIT})"
        )
        return 0

    for failure in failures:
        print(f"DISK FAIL: {failure}", file=sys.stderr)
    return 1


def cli(argv: Sequence[str] | None = None) -> int:
    """Parse the disk checker command line and run its requested read-only mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--classify",
        action="store_true",
        help="print bounded JSON-lines scratch classification without enforcing limits",
    )
    arguments = parser.parse_args(argv)
    if arguments.classify:
        return _print_classification_report()
    return main()


if __name__ == "__main__":
    sys.exit(cli())
