#!/usr/bin/env python3
"""Pre-commit disk check for generated gludd scratch and root disk usage.

Exit 0 = ok, exit 1 = over threshold.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GLUDD_TMP_LIMIT_MB = 100
DISK_USAGE_PCT_LIMIT = 90
TMP_ROOT = Path("/tmp")
WORKTREE_ROOT = TMP_ROOT / "gludd-worktrees"
WORKTREE_GENERATED_DIRS = (".pytest_cache", ".mypy_cache", ".ruff_cache")


def _file_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree_size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    if root.is_file():
        return _file_size_bytes(root)

    total = 0
    try:
        iterator = root.rglob("*")
        for path in iterator:
            if path.is_file():
                total += _file_size_bytes(path)
    except OSError:
        return total
    return total


def _worktree_generated_size_bytes(worktree_root: Path) -> int:
    """Count generated cache dirs inside worktrees, not worktree source files."""
    if not worktree_root.is_dir():
        return 0

    total = 0
    try:
        worktrees = list(worktree_root.iterdir())
    except OSError:
        return 0
    for worktree in worktrees:
        if not worktree.is_dir():
            continue
        for cache_name in WORKTREE_GENERATED_DIRS:
            total += _tree_size_bytes(worktree / cache_name)
    return total


def _gludd_tmp_size_mb(
    tmp_root: Path = TMP_ROOT, worktree_root: Path = WORKTREE_ROOT
) -> float:
    """Return generated /tmp/gludd-* scratch size in MB.

Active git worktree source and worktree-local .venv directories under
    /tmp/gludd-worktrees are intentionally excluded. Small generated tool
    caches inside those worktrees still count against the scratch limit.
    """
    total = 0
    for entry in tmp_root.glob("gludd-*"):
        if entry == worktree_root:
            total += _worktree_generated_size_bytes(worktree_root)
            continue
        total += _tree_size_bytes(entry)
    return total / (1024 * 1024)


def _disk_usage_pct() -> float:
    """Return root disk usage percentage, from 0 to 100."""
    try:
        out = subprocess.check_output(["df", "/"], text=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return 0.0
    parts = out.strip().split("\n")[-1].split()
    if len(parts) < 5:
        return 0.0
    try:
        return float(parts[4].rstrip("%"))
    except ValueError:
        return 0.0


def main() -> int:
    failures: list[str] = []
    tmp_mb = _gludd_tmp_size_mb()
    disk_pct = _disk_usage_pct()

    if tmp_mb > GLUDD_TMP_LIMIT_MB:
        failures.append(
            f"generated /tmp/gludd-* scratch {tmp_mb:.1f} MB > "
            f"{GLUDD_TMP_LIMIT_MB} MB limit"
        )
    if disk_pct > DISK_USAGE_PCT_LIMIT:
        failures.append(f"disk usage {disk_pct:.1f}% > {DISK_USAGE_PCT_LIMIT}% limit")

    if not failures:
        print(
            f"disk ok: generated /tmp/gludd-* scratch = {tmp_mb:.1f} MB "
            f"(<= {GLUDD_TMP_LIMIT_MB}), disk = {disk_pct:.1f}% "
            f"(<= {DISK_USAGE_PCT_LIMIT})"
        )
        return 0

    for failure in failures:
        print(f"DISK FAIL: {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
