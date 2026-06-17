"""Git automation module."""

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import (
    CloneResult,
    GatedCommitResult,
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)

__all__ = [
    "CloneResult",
    "GatedCommitResult",
    "GitAutomation",
    "InitResult",
    "MergeResult",
    "PushResult",
    "WorktreeInfo",
    "WorktreeResult",
]
