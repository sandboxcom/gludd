"""B01/B02/B05: Branch discipline enforcement.

Agent must work on the correct branch, never push feature work to
master, and never merge to master from inside a worktree.
"""

from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"
PLUGIN_DIR = Path(__file__).parent.parent.parent / ".opencode" / "plugin"


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestB01B02B05BranchDiscipline:
    """B01/B02/B05 — branch discipline enforcement."""

    def test_branch_discipline_plugin_exists(self):
        plugin_path = PLUGIN_DIR / "enforce-branch-discipline.ts"
        assert plugin_path.exists(), "B01: enforce-branch-discipline.ts must exist"

    def test_batch_push_verifies_branch(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "batch-push")
        if not recipe:
            return
        assert "branch" in recipe.lower(), "B01: batch-push must verify which branch it pushes"

    def test_verify_state_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "verify-state")
        assert recipe, "B01: verify-state target must exist for branch confirmation"

    def test_worktree_enforcement_plugin_exists(self):
        plugin_path = PLUGIN_DIR / "enforce-worktree.ts"
        assert plugin_path.exists(), "B05: enforce-worktree.ts must exist to prevent pushes/merges from worktrees"

    def test_development_merge_to_master_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "development-merge-to-master")
        assert recipe, (
            "B02: development-merge-to-master must exist as the sanctioned merge path (not direct feature→master)"
        )
