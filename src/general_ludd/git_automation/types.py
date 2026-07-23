"""Data classes for git automation results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InitResult:
    path: str
    created: bool
    message: str = ""


@dataclass
class WorktreeResult:
    path: str
    branch: str
    success: bool
    message: str = ""


@dataclass
class WorktreeInfo:
    path: str
    branch: str
    is_main: bool = False
    commit: str = ""


@dataclass
class MergeResult:
    success: bool
    strategy: str = "ff"
    message: str = ""
    conflicts: list[str] = field(default_factory=list)


@dataclass
class PushResult:
    success: bool
    remote: str = "origin"
    branch: str = ""
    message: str = ""


@dataclass
class CloneResult:
    path: str
    url: str
    success: bool
    already_present: bool = False
    message: str = ""


@dataclass
class GatedCommitResult:
    """Outcome of a gated commit/merge: the change is applied only if the gate
    command exited 0; otherwise ``success`` is False and the tree is unchanged."""
    success: bool
    commit_sha: str | None = None
    gate_returncode: int = 0
    message: str = ""


@dataclass
class GitStateResult:
    success: bool
    branch: str
    head: str
    dirty_count: int = 0
    staged_count: int = 0
    untracked_count: int = 0
    status: list[str] = field(default_factory=list)
    remote: str = "sandboxcom"
    remote_ref: str = ""
    remote_head: str = ""
    master_head: str = ""
    development_head: str = ""
    master_is_ancestor_of_development: bool | None = None


    gha_head_sha: str = ""
    reconciled_preserve_heads: list[str] = field(default_factory=list)
    unintegrated_worktrees: list[dict[str, object]] = field(default_factory=list)
    unintegrated_branches: list[dict[str, object]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
