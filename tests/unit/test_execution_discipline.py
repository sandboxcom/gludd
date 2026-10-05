"""TDD structural tests for Phase EX (Execution Discipline) Makefile targets.

Verifies that push/commit/tag discipline targets exist and encode the correct
defaults and guardrails. These are structural content checks against the
Makefile, not runtime invocations.

Covers Phase EX items: EX.1 (release-tag-push), EX.4 (push-rate-guard),
EX.5 (batch-push threshold), EX.8 (tag specific commit), EX.9 (verify-remote),
plus supporting CP.7, MK.9, MK.10, RL.2, AR.20, PB.7, LM.6, LM.7.
"""

from pathlib import Path

import pytest

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


def _content() -> str:
    """Read the full Makefile content."""
    return MAKEFILE.read_text()


class TestBatchPush:
    """EX.5 / MK.9 / AR.20 / PB.7 — batch-push target with threshold default."""

    def test_target_exists(self) -> None:
        assert _recipe("batch-push"), "batch-push target must exist (EX.5)"

    def test_has_commit_threshold_default(self) -> None:
        recipe = _recipe("batch-push")
        assert "COMMIT_THRESHOLD" in recipe, (
            "batch-push must reference COMMIT_THRESHOLD (EX.5)"
        )

    def test_threshold_default_is_not_one(self) -> None:
        recipe = _recipe("batch-push")
        assert "COMMIT_THRESHOLD:-5" in recipe or "${COMMIT_THRESHOLD:-5}" in recipe, (
            "batch-push default COMMIT_THRESHOLD must be 5 (not 1) (AR.20, PB.7)"
        )

    def test_blocks_threshold_one_override(self) -> None:
        recipe = _recipe("batch-push")
        assert '"1"' in recipe or "= \"1\"" in recipe or "THRESHOLD = " in recipe, (
            "batch-push must reject COMMIT_THRESHOLD=1 override (AR.20)"
        )


class TestShipCommit:
    """ship-commit target — local commit by default (PUSH=0)."""

    def test_target_exists(self) -> None:
        assert _recipe("ship-commit"), "ship-commit target must exist"

    def test_push_default_is_zero(self) -> None:
        content = _content()
        assert "PUSH ?= 0" in content, (
            "ship-commit must default PUSH=0 (no auto-push on commit)"
        )

    def test_does_not_force_push_by_default(self) -> None:
        recipe = _recipe("ship-commit")
        assert '"$(PUSH)" = "1"' in recipe or "PUSH" in recipe, (
            "ship-commit must gate push on PUSH=1 explicitly"
        )


class TestReleaseCut:
    """MK.10 / RL.2 / EX.1 — release-cut requires exact-SHA dual-track evidence."""

    def test_target_exists(self) -> None:
        assert _recipe("release-cut"), "release-cut target must exist (MK.10)"

    def test_calls_require_dual_track_green(self) -> None:
        recipe = _recipe("release-cut")
        assert "require-dual-track-green" in recipe, (
            "release-cut must require local and hosted evidence for one SHA (RL.2)"
        )

    def test_calls_check_readme_status(self) -> None:
        recipe = _recipe("release-cut")
        assert "check-readme-status" in recipe, (
            "release-cut must invoke check-readme-status"
        )

    def test_calls_git_tag_push(self) -> None:
        recipe = _recipe("release-cut")
        assert "git-tag-push" in recipe, (
            "release-cut must invoke git-tag-push to create the release tag"
        )

    def test_requires_TAG_argument(self) -> None:
        recipe = _recipe("release-cut")
        assert "$(TAG)" in recipe, "release-cut must require a TAG argument"


class TestReleaseTagPush:
    """EX.1 / CP.7 / CID.8 — atomic push+tag with CI cancellation."""

    def test_target_exists(self) -> None:
        assert _recipe("release-tag-push"), (
            "release-tag-push target must exist (EX.1, CP.7)"
        )

    def test_cancels_master_ci(self) -> None:
        recipe = _recipe("release-tag-push")
        assert "ci-cancel" in recipe or "ci-active" in recipe, (
            "release-tag-push must cancel master CI runs (CID.8)"
        )

    def test_pushes_master_and_tag(self) -> None:
        recipe = _recipe("release-tag-push")
        assert "git-push-sandboxcom" in recipe, (
            "release-tag-push must push master atomically with tag"
        )
        assert "git-tag-push" in recipe, (
            "release-tag-push must push the tag atomically with master"
        )


class TestCiVerdictSafe:
    """LM.6 / CID.1 — ci-verdict-safe enforces cooldown."""

    def test_target_exists(self) -> None:
        assert _recipe("ci-verdict-safe"), (
            "ci-verdict-safe target must exist (CID.1)"
        )

    def test_invokes_cooldown_script(self) -> None:
        recipe = _recipe("ci-verdict-safe")
        assert "ci_check_cooldown.py" in recipe, (
            "ci-verdict-safe must invoke ci_check_cooldown.py (LM.6)"
        )

    def test_uses_cooldown_env_var(self) -> None:
        recipe = _recipe("ci-verdict-safe")
        assert "CI_CHECK_COOLDOWN_SEC" in recipe, (
            "ci-verdict-safe must reference CI_CHECK_COOLDOWN_SEC"
        )


class TestCiCancel:
    """CID.8 / EX.3 — ci-cancel target exists."""

    def test_target_exists(self) -> None:
        assert _recipe("ci-cancel"), "ci-cancel target must exist (EX.3)"

    def test_accepts_RUN_argument(self) -> None:
        recipe = _recipe("ci-cancel")
        assert "$(RUN)" in recipe, "ci-cancel must accept a RUN=<id> argument"


class TestVerifyRemote:
    """EX.9 / CID.12 — verify-remote target exists."""

    def test_target_exists(self) -> None:
        assert _recipe("verify-remote"), "verify-remote target must exist (EX.9)"

    def test_accepts_branch_and_sha(self) -> None:
        recipe = _recipe("verify-remote")
        assert "SHA" in recipe and "BRANCH" in recipe, (
            "verify-remote must accept SHA= and BRANCH= arguments"
        )

    def test_emits_verified_or_mismatch(self) -> None:
        recipe = _recipe("verify-remote")
        assert "VERIFIED" in recipe, "verify-remote must emit VERIFIED on success"
        assert "MISMATCH" in recipe, "verify-remote must emit MISMATCH on failure"


class TestVerifyReleaseCompleteness:
    """EX.1 / OD.8 — verify-release-completeness target exists."""

    def test_target_exists(self) -> None:
        assert _recipe("verify-release-completeness"), (
            "verify-release-completeness target must exist (OD.8)"
        )

    def test_requires_TAG_argument(self) -> None:
        recipe = _recipe("verify-release-completeness")
        assert "$(TAG)" in recipe, (
            "verify-release-completeness must require a TAG argument"
        )


class TestPushRateGuard:
    """EX.4 / LM.7 — _push-rate-guard enforces cooldown + CI-in-flight."""

    def test_target_exists(self) -> None:
        assert _recipe("_push-rate-guard"), (
            "_push-rate-guard target must exist (EX.4)"
        )

    def test_checks_ci_in_flight(self) -> None:
        recipe = _recipe("_push-rate-guard")
        assert "ci_push_guard.py" in recipe or "ci-busy-check" in recipe, (
            "_push-rate-guard must check for in-flight CI runs (EX.4)"
        )

    def test_references_push_cooldown(self) -> None:
        recipe = _recipe("_push-rate-guard")
        assert "PUSH_COOLDOWN_SECS" in recipe, (
            "_push-rate-guard must reference PUSH_COOLDOWN_SECS (LM.7)"
        )

    def test_push_cooldown_secs_defined(self) -> None:
        content = _content()
        assert "PUSH_COOLDOWN_SECS ?=" in content, (
            "PUSH_COOLDOWN_SECS must be defined as a Makefile variable (LM.7)"
        )

    def test_push_cooldown_secs_nonzero(self) -> None:
        content = _content()
        idx = content.find("PUSH_COOLDOWN_SECS ?=")
        assert idx != -1, "PUSH_COOLDOWN_SECS definition not found"
        line = content[idx:].splitlines()[0]
        value = line.split("?=", 1)[1].strip()
        assert value.isdigit(), f"PUSH_COOLDOWN_SECS must be numeric, got: {value!r}"
        assert int(value) > 0, "PUSH_COOLDOWN_SECS must be > 0"


class TestNoCommitThresholdOneDefault:
    """AR.20 / PB.7 — COMMIT_THRESHOLD=1 must never be the default."""

    def test_no_commit_threshold_default_assignment_to_one(self) -> None:
        """No Make variable default assigns COMMIT_THRESHOLD to 1.

        The literal token ``COMMIT_THRESHOLD=1`` legitimately appears in
        warning/block messages (it is the override the guard rejects). What is
        forbidden is a Make *variable default* of 1: ``?=`` or ``:=``.
        """
        content = _content()
        for bad_pattern in (
            "COMMIT_THRESHOLD ?= 1",
            "COMMIT_THRESHOLD := 1",
        ):
            assert bad_pattern not in content, (
                f"Makefile must not default {bad_pattern!r} (AR.20, PB.7)"
            )

    def test_batch_push_does_not_default_to_one(self) -> None:
        recipe = _recipe("batch-push")
        assert "COMMIT_THRESHOLD:-1" not in recipe, (
            "batch-push must NOT default COMMIT_THRESHOLD to 1 (AR.20)"
        )


@pytest.mark.parametrize(
    "target",
    [
        "batch-push",
        "ship-commit",
        "release-cut",
        "release-tag-push",
        "ci-verdict-safe",
        "ci-cancel",
        "verify-remote",
        "verify-release-completeness",
        "_push-rate-guard",
    ],
)
def test_target_present_in_makefile(target: str) -> None:
    """Each EX-phase target must be present in the Makefile."""
    assert f"\n{target}:" in _content(), (
        f"Makefile target '{target}' must be defined"
    )
