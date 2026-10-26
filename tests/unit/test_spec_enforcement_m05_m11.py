"""M05/M07/M11/M16: canonical integration and retired alias enforcement.

Raw aliases refuse before Git. Canonical development receives one pinned commit;
feature-done retains its atomic no-ff merge and release keeps explicit message
handoff. Native Git preservation is measured by the integration safety cohort.
"""

from pathlib import Path

from scripts.check_make_target_contract import _stanzas
from scripts.makefile_layout import compose_makefile

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_recipe(content: str, target: str) -> str:
    """Read the complete literal rule through the existing Make layout owner."""
    return _stanzas(content).get(target, "")


class TestM05M07M11M16MergeTargets:
    """Preserve history, atomicity and discoverable native compatibility refusal."""

    def test_agent_merge_refuses_before_git_and_names_release_owner(self) -> None:
        recipe = _find_recipe(compose_makefile(MAKEFILE), "agent-merge")
        assert recipe, "M07: retired agent-merge must remain discoverable"
        assert "outcome=retired" in recipe and "native_status=2" in recipe
        assert "exit 2" in recipe and "git merge" not in recipe and "git checkout" not in recipe
        assert "release-promote" in recipe and "REVIEWED_HEAD_INTEGRATION_RECEIPT" in recipe

    def test_agent_merge_dev_refuses_before_git_and_names_pinned_owner(self) -> None:
        recipe = _find_recipe(compose_makefile(MAKEFILE), "agent-merge-dev")
        assert recipe, "M07: retired agent-merge-dev must remain discoverable"
        assert "outcome=retired" in recipe and "native_status=2" in recipe
        assert "exit 2" in recipe and "git merge" not in recipe and "git checkout" not in recipe
        assert "development-merge-forward" in recipe and "development-fast-forward" in recipe

    def test_canonical_receiver_retains_exact_source_history(self) -> None:
        recipe = _find_recipe(compose_makefile(MAKEFILE), "development-fast-forward")
        assert "merge --ff-only" in recipe
        assert "EXPECTED_BEFORE_SHA" in recipe and "EXPECTED_AFTER_SHA" in recipe
        assert "SOURCE_SHA" in recipe and "full SHA-1 values" in recipe
        assert "_gate-mutation-guard" in recipe and "main-worktree-guard" in recipe
        assert "fast-forward result differs from the pinned candidate" in recipe

    def test_feature_done_merges_with_no_ff(self) -> None:
        recipe = _find_recipe(compose_makefile(MAKEFILE), "feature-done")
        assert recipe, "M11: feature-done target must exist"
        assert "--no-ff" in recipe, "M11: feature-done must use --no-ff merge"

    def test_merge_commit_message_convention(self) -> None:
        content = compose_makefile(MAKEFILE)
        retired = _find_recipe(content, "agent-merge")
        assert retired, "M16: retired agent-merge target must exist"
        assert "outcome=retired" in retired and "git merge" not in retired
        release = _find_recipe(content, "release-promote")
        assert 'release-cut TAG="$(TAG)" MSG="$(MSG)"' in release
        assert 'RELEASE_CANDIDATE_SHA="$$DEV_SHA"' in release

    def test_agent_worktree_list_exists(self) -> None:
        assert _find_recipe(compose_makefile(MAKEFILE), "agent-worktree-list"), (
            "M05: agent-worktree-list must exist for diagnostics"
        )

    def test_agent_cleanup_exists(self) -> None:
        assert _find_recipe(compose_makefile(MAKEFILE), "agent-cleanup"), (
            "M05: agent-cleanup must exist for worktree lifecycle"
        )
