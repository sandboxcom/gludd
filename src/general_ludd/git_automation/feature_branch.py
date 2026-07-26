"""Feature branch workflow — feature-start / feature-done / rebranch-onto.

Ports the Makefile ``feature-start``, ``feature-done``, and ``git-rebranch-onto``
targets into reusable Python primitives.  Every function accepts a
``GitAutomation`` instance so callers control the repo path and can inject a mock.
"""

from __future__ import annotations

import subprocess

from general_ludd.git_automation.repo import GitAutomation, _reject_leading_dash
from general_ludd.git_automation.types import MergeResult


def _validate_branch_name(name: str) -> str:
    stripped = (name or "").strip()
    if not stripped:
        raise ValueError("branch name must not be empty")
    _reject_leading_dash(stripped, kind="branch name")
    return stripped


def _full_branch_name(name: str) -> str:
    return name if "/" in name else f"feature/{name}"


def feature_start(
    *,
    git: GitAutomation,
    name: str,
    base: str = "master",
) -> str:
    """Create and switch to a feature branch.

    Args:
        git: ``GitAutomation`` instance pointing at the repo.
        name: Short branch name.  ``feature/`` is prepended automatically
              unless the name already contains a ``/``.
        base: Existing branch to branch from (default ``master``).

    Returns:
        The full branch name (e.g. ``"feature/my-feature"``).

    Raises:
        ValueError: If ``name`` is invalid or the branch already exists.
    """
    clean = _validate_branch_name(name)
    full = _full_branch_name(clean)

    existing = git.list_branches()
    if full in existing:
        raise ValueError(f"branch {full!r} already exists")

    git._run_git("checkout", base, "--")
    return git.create_branch(full)


def feature_done(
    *,
    git: GitAutomation,
    name: str,
    target: str = "master",
) -> dict[str, object]:
    """Merge a feature branch into the target and delete it.

    The working tree MUST be clean; the merge uses ``--no-ff`` so the
    feature branch history is preserved in a merge commit.

    Args:
        git: ``GitAutomation`` instance pointing at the repo.
        name: Short branch name (auto-prefixed with ``feature/`` if needed).
        target: Branch to merge into (default ``master``).

    Returns:
        ``{"success": True, "branch": <name>, "target": <target>, "message": ...}``

    Raises:
        ValueError: If the tree is dirty or the feature branch does not exist.
        RuntimeError: If the merge fails (e.g. conflicts).
    """
    clean = _validate_branch_name(name)
    full = _full_branch_name(clean)

    state = git.workflow_state(assert_clean=True)
    if not state.success:
        raise ValueError(
            "working tree is not clean; commit or stash before merging "
            f"({'; '.join(state.errors)})"
        )

    existing = git.list_branches()
    if full not in existing:
        raise ValueError(f"feature branch {full!r} not found")

    result: MergeResult = git.merge_branch(
        git.repo_path, full, target, strategy="no-ff",
    )
    if not result.success:
        raise RuntimeError(
            f"merge of {full} into {target} failed: {result.message} "
            f"(conflicts: {result.conflicts})"
        )

    git.delete_branch(full)

    return {
        "success": True,
        "branch": full,
        "target": target,
        "message": result.message,
    }


def rebranch_onto(
    *,
    git: GitAutomation,
    new_base: str,
    commits: list[str] | None = None,
) -> dict[str, object]:
    """Create a new branch at ``new_base`` and cherry-pick ``commits`` onto it.

    The current branch's name is embedded in the new branch name so the
    intent is obvious (``rebranch-<orig>-onto-<shortbase>``).  On any
    cherry-pick failure the whole operation is rolled back: the new branch
    is deleted and the working tree is restored to the original branch.

    Args:
        git: ``GitAutomation`` instance pointing at the repo.
        new_base: Commit-ish to root the new branch on.
        commits: List of commit SHAs to cherry-pick in order.
                 If omitted no cherry-picks are performed (the new branch
                 starts clean at ``new_base``).

    Returns:
        ``{"success": True, "branch": <new_name>, "base": <new_base>,
          "original": <old_branch>}``

    Raises:
        ValueError: If ``new_base`` is not a valid commit or is option-injection.
        RuntimeError: If a cherry-pick conflicts.
    """
    _reject_leading_dash(new_base, kind="base ref")
    try:
        git._run_git("rev-parse", "--verify", f"{new_base}^{{commit}}")
    except subprocess.CalledProcessError:
        raise ValueError(f"{new_base!r} is not a valid commit") from None

    orig_branch = git.current_branch()
    short_base = new_base[:7]
    new_branch = f"rebranch-{orig_branch}-onto-{short_base}"

    git._run_git("checkout", "-b", new_branch, new_base)

    if commits:
        for c in commits:
            try:
                git._run_git("cherry-pick", c)
            except subprocess.CalledProcessError:
                git._run_git("cherry-pick", "--abort", check=False)
                git._run_git("checkout", "-f", orig_branch, check=False)
                git._run_git("branch", "-D", new_branch, check=False)
                raise RuntimeError(
                    f"cherry-pick {c} conflicted; rebranch from {orig_branch} "
                    f"onto {new_base} aborted and cleaned up"
                ) from None

    return {
        "success": True,
        "branch": new_branch,
        "base": new_base,
        "original": orig_branch,
    }
