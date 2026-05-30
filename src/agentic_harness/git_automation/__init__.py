"""Git automation module."""

from agentic_harness.git_automation.repo import GitAutomation
from agentic_harness.git_automation.types import (
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
