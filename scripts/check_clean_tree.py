#!/usr/bin/env python3
"""Fail closed when a git worktree has uncommitted changes."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence

RunFn = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
    )


def dirty_lines(status_output: str) -> list[str]:
    """Return non-empty git porcelain status lines."""
    return [line for line in status_output.splitlines() if line.strip()]


def check_clean_tree(run: RunFn = _run) -> int:
    """Exit code for a clean-tree gate."""
    result = run(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git status failed").strip()
        print(f"BLOCKED: unable to verify clean tree: {detail}", file=sys.stderr)
        return 1

    dirty = dirty_lines(result.stdout)
    if not dirty:
        print("check-clean-tree: clean")
        return 0

    print("BLOCKED: working tree is dirty; commit or restore changes before push, release, or CI dispatch.")
    for line in dirty[:50]:
        print(f"  {line}")
    if len(dirty) > 50:
        print(f"  ... {len(dirty) - 50} more changed path(s)")
    return 1


def main() -> int:
    return check_clean_tree()


if __name__ == "__main__":
    raise SystemExit(main())
