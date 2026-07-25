"""Git automation module."""

from general_ludd.git_automation.release_ops import (
    release_cut,
    release_delete,
    release_recut,
    verify_readme_status,
)
from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import (
    BatchPushResult,
    CloneResult,
    GatedCommitResult,
    InitResult,
    MergeResult,
    PushResult,
    ReleaseCutResult,
    ReleaseDeleteResult,
    ReleaseRecutResult,
    WorktreeInfo,
    WorktreeResult,
)
from general_ludd.git_automation.worktree import (
    WorktreeHealthViolation,
    worktree_cleanup,
    worktree_create,
    worktree_health_check,
    worktree_list,
    worktree_merge,
    worktree_merge_all,
)

__all__ = [
    "BatchPushResult",
    "CloneResult",
    "GatedCommitResult",
    "GitAutomation",
    "InitResult",
    "MergeResult",
    "PushResult",
    "ReleaseCutResult",
    "ReleaseDeleteResult",
    "ReleaseRecutResult",
    "WorktreeHealthViolation",
    "WorktreeInfo",
    "WorktreeResult",
    "release_cut",
    "release_delete",
    "release_recut",
    "verify_readme_status",
    "worktree_cleanup",
    "worktree_create",
    "worktree_health_check",
    "worktree_list",
    "worktree_merge",
    "worktree_merge_all",
]
