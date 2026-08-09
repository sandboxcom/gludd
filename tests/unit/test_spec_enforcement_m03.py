"""M03: Pre-merge gate must be green.

Before merging any branch into a shared branch, the gate MUST be
green on the source branch. Merge targets must check for gate-status
freshness or run CI checks before merging.
"""

from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_target_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestM03PreMergeGateGreen:
    """M03 — merge targets verify gate or CI before merging."""

    def test_development_merge_to_master_checks_ci(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "development-merge-to-master")
        assert recipe, "M03: development-merge-to-master must exist"
        assert "merge-ready" in recipe or "require-ci-green" in recipe, (
            "M03: development-merge-to-master must check merge readiness or require CI green before merging"
        )

    def test_merge_ready_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "merge-ready")
        assert recipe, "M03: merge-ready target must exist"
        assert "assert-clean" in recipe or "assert-merge-ready" in recipe, (
            "M03: merge-ready must verify clean tree and merge readiness"
        )

    def test_gated_merge_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "gated-merge")
        assert recipe, "M03: gated-merge target must exist"

    def test_ship_async_checks_gate_before_merge(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "ship-async")
        assert recipe, "M03: ship-async target must exist"

    def test_feature_done_runs_tests_before_merge(self):
        content = MAKEFILE.read_text()
        recipe = _find_target_recipe(content, "feature-done")
        assert recipe, "M03: feature-done target must exist"
        assert "pytest" in recipe or "test" in recipe, "M03: feature-done must run tests before merging"
