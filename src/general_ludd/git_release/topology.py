"""Branch topology summary — read-only input for the ``branch_plan`` role.

Surfaces branch list, worktree list, and HEAD-vs-trunk ancestry in a single
JSON-serializable dict. Never mutates the repository.
"""

from __future__ import annotations

from general_ludd.git_automation.repo import GitAutomation

_TRUNK_BRANCHES = ("master", "main", "development")


def describe_topology(
    repo_path: str = ".",
    *,
    git: GitAutomation | None = None,
) -> dict[str, object]:
    """Return a read-only branch+worktree topology summary for ``repo_path``.

    Keys: ``branches``, ``trunk_branches``, ``current_branch``, ``head_sha``,
    ``worktrees``, ``head_is_on_trunk``. The dict is JSON-serializable for
    consumption by the ``branch_plan`` ansible role.
    """
    git = git if git is not None else GitAutomation(repo_path)
    branches = git.list_branches()
    current = git.current_branch()
    head_sha = git.get_current_commit()
    worktrees = [
        {
            "path": wt.path,
            "branch": wt.branch.removeprefix("refs/heads/"),
            "head_sha": wt.commit,
            "is_main": wt.is_main,
        }
        for wt in git.list_worktrees(repo_path)
    ]
    return {
        "branches": branches,
        "trunk_branches": [b for b in branches if b in _TRUNK_BRANCHES],
        "current_branch": current,
        "head_sha": head_sha,
        "worktrees": worktrees,
        "head_is_on_trunk": current in _TRUNK_BRANCHES,
    }
