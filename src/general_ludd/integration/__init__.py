"""Integration primitives for promoting agent/self-improve worktree results.

This package holds the logic gludd uses to fold the output of an agent or
self-improvement worktree back into a base repo WITHOUT the whole-file-copy
data-loss bug: a blind copy reverts any change the base made to a file the
worktree also touched. See :mod:`general_ludd.integration.safe_merge`.
"""

from __future__ import annotations

from general_ludd.integration.safe_merge import (
    MergeResult,
    detect_overlap,
    safe_merge,
    safe_merge_file,
)

__all__ = [
    "MergeResult",
    "detect_overlap",
    "safe_merge",
    "safe_merge_file",
]
