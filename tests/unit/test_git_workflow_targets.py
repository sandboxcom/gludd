"""TDD structural tests for git workflow recovery/safety Makefile targets.

Targets added 2026-07-19: git-cherry-pick, git-merge-abort, git-rebase-abort,
git-reset-hard. These prevent the "botched merge with no recovery command"
class of failure — the agent must be able to abort a bad merge/rebase with a
single make target instead of running bare git commands or being stuck.
"""
from __future__ import annotations

import re
from pathlib import Path

MAKEFILE_PATH = Path(__file__).resolve().parents[2] / "Makefile"


def _makefile_src() -> str:
    return MAKEFILE_PATH.read_text()


class TestGitWorkflowTargetExistence:
    """Each recovery/safety target must exist in the Makefile."""

    def test_git_merge_abort_exists(self):
        assert re.search(r"^git-merge-abort:", _makefile_src(), re.MULTILINE), (
            "git-merge-abort target missing from Makefile"
        )

    def test_git_rebase_abort_exists(self):
        assert re.search(r"^git-rebase-abort:", _makefile_src(), re.MULTILINE), (
            "git-rebase-abort target missing from Makefile"
        )

    def test_git_reset_hard_exists(self):
        assert re.search(r"^git-reset-hard:", _makefile_src(), re.MULTILINE), (
            "git-reset-hard target missing from Makefile"
        )

    def test_git_cherry_pick_exists(self):
        assert re.search(r"^git-cherry-pick:", _makefile_src(), re.MULTILINE), (
            "git-cherry-pick target missing from Makefile"
        )


class TestGitWorkflowTargetsInPhony:
    """All new targets must be in .PHONY."""

    def test_all_four_in_phony(self):
        makefile = _makefile_src()
        # Find the MAIN .PHONY block (near the top, contains git- targets)
        for m in re.finditer(r"\.PHONY:.*?(?=\n\n\S|\n[a-z_-]+:|\Z)", makefile, re.DOTALL):
            phony_text = m.group(0)
            if "git-status" in phony_text:
                break
        else:
            raise AssertionError("Main .PHONY block (containing git-status) not found")
        for target in ("git-merge-abort", "git-rebase-abort",
                       "git-reset-hard", "git-cherry-pick"):
            assert target in phony_text, f"{target} not in .PHONY list"


class TestGitWorkflowTargetRecipeContent:
    """Each target must use the correct git command and parameters."""

    def _recipe(self, target: str) -> str | None:
        m = re.search(
            rf"{re.escape(target)}:\n(.*?)(?=\n[a-zA-Z_-]+:|\Z)",
            _makefile_src(), re.DOTALL,
        )
        return m.group(1) if m else None

    def test_merge_abort_uses_git_merge_abort(self):
        recipe = self._recipe("git-merge-abort")
        assert recipe, "git-merge-abort recipe not found"
        assert "git merge --abort" in recipe, (
            "git-merge-abort must use 'git merge --abort'"
        )

    def test_rebase_abort_uses_git_rebase_abort(self):
        recipe = self._recipe("git-rebase-abort")
        assert recipe, "git-rebase-abort recipe not found"
        assert "git rebase --abort" in recipe, (
            "git-rebase-abort must use 'git rebase --abort'"
        )

    def test_reset_hard_requires_msg(self):
        recipe = self._recipe("git-reset-hard")
        assert recipe, "git-reset-hard recipe not found"
        assert "-z" in recipe and "$(MSG)" in recipe, (
            "git-reset-hard must require MSG= variable"
        )

    def test_reset_hard_uses_git_reset_hard(self):
        recipe = self._recipe("git-reset-hard")
        assert recipe, "git-reset-hard recipe not found"
        assert "git reset --hard" in recipe, (
            "git-reset-hard must use 'git reset --hard'"
        )

    def test_cherry_pick_requires_var(self):
        recipe = self._recipe("git-cherry-pick")
        assert recipe, "git-cherry-pick recipe not found"
        assert "-z" in recipe and "$(SHA)" in recipe, (
            "git-cherry-pick must require SHA= variable"
        )

    def test_cherry_pick_uses_git_cherry_pick(self):
        recipe = self._recipe("git-cherry-pick")
        assert recipe, "git-cherry-pick recipe not found"
        assert "git cherry-pick" in recipe, (
            "git-cherry-pick must use 'git cherry-pick'"
        )

    def test_documented_targets_all_exist(self):
        """All 17 targets listed in AGENTS.md git workflow section must exist."""
        documented = [
            "git-status", "git-diff", "git-staged", "git-log", "git-show",
            "git-add", "git-add-all", "git-commit", "git-reset",
            "git-branch", "git-checkout", "git-merge", "git-stash",
            "git-stash-pop", "git-rm", "git-mv", "git-rebranch-onto",
        ]
        makefile_src = _makefile_src()
        missing = [t for t in documented
                   if not re.search(rf"^{t}:", makefile_src, re.MULTILINE)]
        assert not missing, (
            f"AGENTS.md-documented targets missing from Makefile: {missing}"
        )
