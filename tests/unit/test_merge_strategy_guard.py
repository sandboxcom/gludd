"""Makefile: Verify merge-strategy-guard exists and enforces safety.

Validates that _merge-strategy-guard exists and is referenced by
merge targets, ensuring no unsafe merge can proceed without checks.
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


class TestMergeStrategyGuard:
    """Verify merge safety mechanisms are in place."""

    def test_merge_strategy_guard_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "_merge-strategy-guard")
        assert recipe, "merge safety: _merge-strategy-guard must exist"

    def test_git_merge_references_guard(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "git-merge")
        assert recipe, "git-merge target must exist"
        assert "_merge-strategy-guard" in recipe, "git-merge must reference _merge-strategy-guard as prerequisite"

    def test_feature_done_references_gate(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "feature-done")
        assert recipe, "feature-done must exist"
        assert "pytest" in recipe, "feature-done must run tests before merge"

    def test_merge_ready_includes_assert_clean(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "merge-ready")
        assert recipe, "merge-ready must exist"
        assert "assert-clean" in recipe, "merge-ready must assert clean working tree"
