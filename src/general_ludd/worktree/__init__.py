"""Worktree monitor — detects abandoned git worktrees with AGENTS.md and creates todos."""

from general_ludd.worktree.core import (
    AgentsMdResult,
    TrackedWorktree,
    WorktreeEventDispatcher,
    WorktreeMonitor,
    WorktreeMonitorConfig,
    WorktreeScanner,
    is_git_worktree,
    parse_agents_md_markdown,
)

__all__ = [
    "AgentsMdResult",
    "TrackedWorktree",
    "WorktreeEventDispatcher",
    "WorktreeMonitor",
    "WorktreeMonitorConfig",
    "WorktreeScanner",
    "is_git_worktree",
    "parse_agents_md_markdown",
]
