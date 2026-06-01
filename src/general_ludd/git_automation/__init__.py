"""Git automation module."""

from general_ludd.git_automation.repo import GitAutomation
from general_ludd.git_automation.types import (
    InitResult,
    MergeResult,
    PushResult,
    WorktreeInfo,
    WorktreeResult,
)

__all__ = [
    "GitAutomation",
    "InitResult",
    "MergeResult",
    "PushResult",
    "WorktreeInfo",
    "WorktreeResult",
]
