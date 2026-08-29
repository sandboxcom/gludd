"""Structural tests for the presence of key release Makefile targets.

Verifies that `release-create`, `release-cut`, and `release-recut` targets
exist in the Makefile. Uses the same Makefile-scanning style as
test_commit_gate_freshness.py and test_release_recut_target.py.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target. Assert target exists."""
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestReleaseTargetsExist:
    """Required release-shaped targets must be present in the Makefile."""

    def test_release_cut_target_exists(self) -> None:
        assert _recipe("release-cut"), "release-cut target must exist"

    def test_release_recut_target_exists(self) -> None:
        assert _recipe("release-recut"), "release-recut target must exist"

    def test_release_create_target_exists(self) -> None:
        """release-create is the manual fallback for publishing a GitHub Release."""
        assert _recipe("release-create"), "release-create target must exist"

    def test_release_create_invokes_build_and_upload(self) -> None:
        """release-create must build artifacts locally and upload via gh."""
        recipe = _recipe("release-create")
        assert "build-executable" in recipe, (
            "release-create must build artifacts via build-executable"
        )
        assert "gh release" in recipe, (
            "release-create must create/upload the release via gh"
        )

    def test_release_create_is_ci_gated_and_draft_only(self) -> None:
        """release-create must be CI-gated and publish draft-only, pointing the
        operator at verify-release-completeness to finish the release.

        release-create cannot produce the full artifact matrix (only CI can,
        via the per-platform build jobs), so the old `verify-release-artifact`
        check (non-draft + >=1 asset) was too weak: v0.1.0-beta.1 shipped
        publicly with 1/12 assets on a RED SHA through the old ungated version
        of this target. The new contract is stronger: gate on CI-green before
        building/publishing anything, always create the release as a draft
        (never auto-publish a partial release), and direct the operator to run
        verify-release-completeness before un-drafting.
        """
        recipe = _recipe("release-create")
        assert "require-ci-green" in recipe, (
            "release-create must gate on CI-green before publishing "
            "(AGENTS.md: Release Pipeline Must Be CI-Green)"
        )
        assert "--draft" in recipe, (
            "release-create must publish as a draft — it cannot produce the "
            "full artifact matrix on its own, so it must never auto-publish "
            "a partial release"
        )
        assert "verify-release-completeness" in recipe, (
            "release-create must direct the operator to verify-release-completeness "
            "before the draft can be un-drafted"
        )


class TestReleaseCutPipeline:
    """release-cut requires exact-SHA dual-track evidence before publication.

    Per AGENTS.md:
      - local and hosted canonical lanes must both be green for one exact SHA;
        ``require-dual-track-green`` is step 0 and aborts on missing evidence.
      - "A Release is an Artifact, Not a Tag": release-cut calls verify-release-artifact
        as the final step with a poll loop (async CI artifact publication).
    """

    def test_require_ci_green_target_exists(self) -> None:
        """make require-ci-green [SHA=...] must exist as a callable target."""
        assert _recipe("require-ci-green"), (
            "require-ci-green target must exist (wraps scripts/require_ci_green.py)"
        )

    def test_release_cut_invokes_require_dual_track_green(self) -> None:
        """Exact-SHA dual-track evidence must precede every push/tag step."""
        recipe = _recipe("release-cut")
        assert "require-dual-track-green" in recipe, (
            "release-cut must invoke require-dual-track-green as step 0 "
            "(AGENTS.md: local and hosted evidence must match one exact SHA)"
        )

    def test_release_cut_dual_track_green_precedes_push(self) -> None:
        """Dual-track evidence must appear before git-push-sandboxcom."""
        recipe = _recipe("release-cut")
        ci_pos = recipe.find("require-dual-track-green")
        push_pos = recipe.find("git-push-sandboxcom")
        assert ci_pos != -1 and push_pos != -1, (
            "release-cut must reference both require-dual-track-green and "
            "git-push-sandboxcom"
        )
        assert ci_pos < push_pos, (
            "release-cut must run require-dual-track-green before publication"
        )

    def test_release_cut_invokes_verify_release_artifact(self) -> None:
        """verify-release-artifact must be invoked as the final step."""
        recipe = _recipe("release-cut")
        assert "verify-release-artifact" in recipe, (
            "release-cut must invoke verify-release-artifact as the final step "
            "(AGENTS.md: A Release is an Artifact, Not a Tag)"
        )

    def test_release_cut_verify_follows_tag_push(self) -> None:
        """verify-release-artifact must appear AFTER git-tag-push in the recipe."""
        recipe = _recipe("release-cut")
        verify_pos = recipe.find("verify-release-artifact")
        tag_pos = recipe.find("git-tag-push")
        assert verify_pos != -1 and tag_pos != -1, (
            "release-cut must reference both git-tag-push and verify-release-artifact"
        )
        assert verify_pos > tag_pos, (
            "release-cut must run verify-release-artifact AFTER git-tag-push "
            "(artifact verification follows tag publication)"
        )

    def test_release_cut_poll_loop_emits_progress(self) -> None:
        """The verify-release-artifact poll loop must print per-attempt progress.

        Per AGENTS.md 'No Unseen Events' rule: a long-running poll must emit a
        heartbeat / progress marker, never run silently.
        """
        recipe = _recipe("release-cut")
        assert "attempt" in recipe or "Waiting" in recipe, (
            "release-cut verify poll must emit per-attempt progress "
            "(AGENTS.md: No Unseen Events)"
        )

    def test_release_cut_preserves_existing_steps(self) -> None:
        """The original 4 steps must remain intact (only step 0 + final added)."""
        recipe = _recipe("release-cut")
        for step in (
            "check-readme-status",
            "git-push-sandboxcom",
            "git-tag-push",
            "release-view",
        ):
            assert step in recipe, (
                f"release-cut must still invoke {step} "
                "(existing step must be preserved)"
            )

    def test_release_cut_uses_submake(self) -> None:
        """Each sub-target invocation must use $(MAKE) for portability."""
        recipe = _recipe("release-cut")
        assert "$(MAKE)" in recipe, (
            "release-cut must invoke sub-targets via $(MAKE), not bare make"
        )
