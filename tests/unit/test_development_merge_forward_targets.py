"""Structural pins for the S83 branch-reconciliation Makefile targets.

These assertions hold the recipe contracts so a refactor cannot silently
drop the transactional guards on development merge-forward, the patch
equivalence validation, the development-only conflict resolver, or the
worktree merge fan-in path.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_TARGET_RE = r"^(?P<name>%s):\n(?P<recipe>(?:\t.*(?:\n|$))+)"


def _makefile() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _recipe(target: str) -> str:
    makefile = _makefile()
    match = re.search(_TARGET_RE % re.escape(target), makefile, re.MULTILINE)
    assert match, f"{target} target is missing"
    return match.group("recipe")


def test_development_merge_forward_target_exists() -> None:
    assert re.search(r"^development-merge-forward:\n", _makefile(), re.MULTILINE), (
        "development-merge-forward target is missing"
    )


def test_development_merge_forward_recipe_has_merge_head_abort_trap() -> None:
    recipe = _recipe("development-merge-forward")

    assert "abort_merge" in recipe
    assert "trap abort_merge EXIT HUP INT TERM" in recipe
    assert "merge --abort" in recipe
    assert "MERGE_HEAD" in recipe


def test_development_merge_forward_recipe_runs_collect_check() -> None:
    recipe = _recipe("development-merge-forward")

    assert "collect-check" in recipe
    assert "Collection check failed; aborting transaction" in recipe


def test_development_merge_forward_recipe_selects_strategy_per_mode() -> None:
    recipe = _recipe("development-merge-forward")

    assert "-X ours" in recipe
    assert "merge --no-ff -s ours --no-commit" in recipe
    assert "MODE must be explicitly set to content or ancestry-only" in recipe


def test_development_merge_forward_apply_requires_development_branch() -> None:
    recipe = _recipe("development-merge-forward")

    assert '"$$CURRENT_BRANCH" != "development"' in recipe
    assert "requires current branch development" in recipe
    assert "requires a clean development worktree" in recipe


def test_git_patch_equivalence_exists_and_validates_variables() -> None:
    recipe = _recipe("git-patch-equivalence")

    for fragment in (
        '"$$PATCH_UPSTREAM"',
        '"$$PATCH_HEAD"',
        '"$$PATCH_LIMIT"',
        "PATCH_LIMIT must be a non-negative integer",
        "git cherry",
        "patch-equivalent=",
    ):
        assert fragment in recipe, f"missing {fragment!r} in git-patch-equivalence recipe"


def test_resolve_development_conflicts_exists_and_refuses_other_branches() -> None:
    recipe = _recipe("resolve-development-conflicts")

    assert '"$$MERGE_SOURCE"' in recipe
    assert '"$$BRANCH" = "development"' in recipe
    assert "Refusing conflict resolution on branch" in recipe
    assert "expected development" in recipe


def test_branches_unmerged_target_exists() -> None:
    assert re.search(r"^branches-unmerged:\n", _makefile(), re.MULTILINE), "branches-unmerged target is missing"


def test_agent_merge_dev_exists_and_uses_no_ff() -> None:
    recipe = _recipe("agent-merge-dev")

    assert "git checkout development" in recipe
    assert "git merge --no-ff" in recipe
    assert "into development" in recipe
