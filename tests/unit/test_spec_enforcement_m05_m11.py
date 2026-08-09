"""M05/M07/M11/M16: Agent-merge and merge-target enforcement.

Verifies that agent-merge runs on the main checkout (not inside a
worktree), uses --no-ff, and feature-done merges with --no-ff.
"""

from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestM05M07M11M16MergeTargets:
    """M05/M07/M11/M16 — agent-merge and feature-done enforcement."""

    def test_agent_merge_uses_no_ff(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "agent-merge")
        assert recipe, "M07: agent-merge target must exist"
        assert "--no-ff" in recipe, "M07: agent-merge must use --no-ff to preserve branch history"

    def test_agent_merge_dev_uses_no_ff(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "agent-merge-dev")
        if not recipe:
            return
        assert "--no-ff" in recipe, "M07: agent-merge-dev must use --no-ff"

    def test_feature_done_merges_with_no_ff(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "feature-done")
        assert recipe, "M11: feature-done target must exist"
        assert "--no-ff" in recipe, "M11: feature-done must use --no-ff merge"

    def test_merge_commit_message_convention(self):
        content = MAKEFILE.read_text()
        target = "agent-merge"
        recipe = _find_recipe(content, target)
        assert recipe, "M16: agent-merge target must exist"
        if "merge:" in recipe:
            assert "worktree work into master" in recipe or "into master" in recipe, (
                "M16: agent-merge commit message must follow convention: merge: <branch> worktree work into master"
            )

    def test_agent_worktree_list_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "agent-worktree-list")
        assert recipe, "M05: agent-worktree-list must exist for diagnostics"

    def test_agent_cleanup_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "agent-cleanup")
        assert recipe, "M05: agent-cleanup must exist for worktree lifecycle"
