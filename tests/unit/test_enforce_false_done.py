"""Tests for the release-artifact enforcement in the false-completion guardrail.

Verifies that shipped/released/deployed claims REQUIRE release-specific
evidence (verify-release-artifact PASS, VERIFIED line, gh release view)
and are blocked when only general evidence (commit SHA, gate pass, test
count) is present.

This is the test for BUGS.md 2026-06-30 Fix #4: wire verify-release-artifact
into the completion gate so a version can't be declared done without an
actual downloadable artifact.

TDD: this file was written FIRST to assert the new release-claim behavior
before the plugin was patched.
"""

import re

from tests.unit._plugin_contract import plugin_contract_source

ROOT = __import__("pathlib").Path(__file__).parent.parent.parent

# Ported from the plugin source — must stay in sync with:
#   RELEASE_CLAIM_PATTERNS
#   RELEASE_EVIDENCE_PATTERNS
#   CLAIM_PATTERNS
#   EVIDENCE_PATTERNS
#   HEDGE_PATTERNS

RELEASE_CLAIM = [
    r"\bshipped\b",
    r"\breleased\b",
    r"\bdeployed\b",
]

RELEASE_EVIDENCE = [
    r"VERIFIED\s+\S+@[0-9a-f]{7,40}",
    r"verify-release-artifact[^\n]{0,80}PASS",
    r"ARTIFACT\s+CHECK:\s*PASS",
    r"gh release view",
]

# General evidence — satisfied by a commit SHA, not sufficient for releases
GENERAL_EVIDENCE = [
    r"\bcommit\s+(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}\b",
    r"\bsha[:= ]\s*[0-9a-f]{7,40}\b",
    r"\.gate-status",
    r"\b[1-9]\d*\s+passed\b",
    r"ci-verdict",
    r"conclusion:\s*success",
]

_NOT_PAT = r"not (?:yet |fully )?(?:done|live|complete|completed|committed|pushed"
_NOT_PAT += r"|built|working|applied|landed|shipped|wired|verified)"

HEDGE = [
    _NOT_PAT,
    r"\bnot yet\b",
    r"\bin progress\b",
    r"\buncommitted\b",
    r"\bunpushed\b",
    r"\bpending\b",
    r"\bunverified\b",
    r"\bnot applied\b",
    r"\bnot built\b",
    r"\bblocked\b",
    r"\bnext steps?\b",
    r"\bstill needs?\b",
]


def _has(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_false_done(text):
    """Python port of the plugin's classify() function including release gating.

    Must mirror the TypeScript plugin's classify() logic exactly:
      1. If no claim → not false-done
      2. If hedge → not false-done
      3. If release claim AND no release evidence → false-done (blocked)
      4. If general evidence → not false-done
      5. Otherwise → false-done
    """
    if not _has(RELEASE_CLAIM, text) and not _has([r"\bdone\b", r"\bfixed\b",
        r"\blanded\b", r"\bcomplete\b", r"\bresolved\b", r"\bworking\b",
        r"\bsuccessful\b", r"\ball green\b", r"✅", r"\bmerged\b", r"\bwired\b",
        r"\boperational\b", r"\bfunctional\b", r"\bproduction[- ]ready\b",
        r"\bgood to go\b", r"\bnow works\b"], text):
        return False
    if _has(HEDGE, text):
        return False
    if _has(RELEASE_CLAIM, text):
        return not _has(RELEASE_EVIDENCE, text)
    return not _has(GENERAL_EVIDENCE, text)


class TestReleaseClaimWithoutArtifactEvidence:
    """A shipped/released/deployed claim without release-specific evidence
    (verify-release-artifact PASS, VERIFIED line, gh release view) is
    blocked — EVEN IF general evidence (commit SHA, gate pass, test count)
    is present."""

    def test_shipped_with_only_commit_sha_is_blocked(self):
        """'shipped v0.1.0-alpha.5, commit abc1234' — general evidence but no artifact."""
        text = "Shipped v0.1.0-alpha.5. Commit abc1234 — tests pass, gate green."
        assert is_false_done(text), (
            "A shipped claim with a commit SHA but no verify-release-artifact PASS "
            "or VERIFIED line MUST be blocked. Commit hashes are not release evidence."
        )

    def test_shipped_with_gate_pass_is_blocked(self):
        """'shipped. .gate-status PASS' — gate pass is not release evidence."""
        text = "All shipped. .gate-status PASS, lint 0 errors."
        assert is_false_done(text), (
            "A shipped claim with only .gate-status MUST be blocked — the gate "
            "is not release verification."
        )

    def test_shipped_with_test_count_is_blocked(self):
        """'shipped. 5000 passed.' — test count is not release evidence."""
        text = "Release shipped. 5000 passed in the test suite."
        assert is_false_done(text), (
            "A shipped claim with only a pass count MUST be blocked — "
            "test counts are not release evidence."
        )

    def test_released_without_artifact_evidence_is_blocked(self):
        text = "Released v0.1.0-alpha.5 to GitHub."
        assert is_false_done(text), (
            "A released claim without any release-specific evidence MUST be blocked."
        )

    def test_deployed_with_ci_verdict_is_blocked(self):
        """CI-green is general evidence, not release evidence."""
        text = "Deployed. make ci-verdict BRANCH=master → conclusion: success"
        assert is_false_done(text), (
            "A deployed claim with ci-verdict but no artifact evidence MUST be blocked — "
            "CI-green does not prove a release artifact exists."
        )


class TestReleaseClaimWithArtifactEvidence:
    """A shipped/released/deployed claim WITH release-specific evidence passes."""

    VERSION = "v0.1.0-alpha.5"
    SHA = "a1b2c3d"

    def test_shipped_with_verify_release_artifact_pass_passes(self):
        text = (
            f"Shipped {self.VERSION}. "
            f"make verify-release-artifact TAG={self.VERSION} PASS — "
            f"11 published assets."
        )
        assert not is_false_done(text), (
            "A shipped claim WITH verify-release-artifact PASS MUST pass."
        )

    def test_shipped_with_verified_line_and_artifact_pass_passes(self):
        text = (
            f"Shipped {self.VERSION}. "
            f"VERIFIED master@{self.SHA} "
            f"verify-release-artifact TAG={self.VERSION} PASS"
        )
        assert not is_false_done(text), (
            "A shipped claim with BOTH VERIFIED @sha AND verify-release-artifact "
            "PASS MUST pass."
        )

    def test_shipped_with_artifact_check_pass_passes(self):
        text = (
            f"Shipped {self.VERSION}. "
            f"ARTIFACT CHECK: PASS — v0.1.0-alpha.5 has 11 published asset(s) on sandboxcom/gludd."
        )
        assert not is_false_done(text), (
            "A shipped claim with ARTIFACT CHECK: PASS MUST pass."
        )

    def test_shipped_with_gh_release_view_passes(self):
        text = (
            f"Shipped {self.VERSION}. "
            f"gh release view shows isDraft:false, assets: 11 — url: https://github.com/sandboxcom/gludd/releases/tag/{self.VERSION}"
        )
        assert not is_false_done(text), (
            "A shipped claim with gh release view output MUST pass."
        )

    def test_verified_line_alone_is_release_evidence(self):
        text = f"Released {self.VERSION}. VERIFIED master@{self.SHA}"
        assert not is_false_done(text), (
            "A released claim with a VERIFIED line MUST pass — "
            "VERIFIED is release-specific evidence."
        )


class TestReleaseClaimHedgePasses:
    """A shipped/released/deployed claim with an honest hedge passes even
    without artifact evidence."""

    def test_not_yet_shipped_passes(self):
        text = "The feature is done but not yet shipped."
        assert not is_false_done(text), (
            "'not yet shipped' is an honest hedge — MUST pass."
        )

    def test_pending_release_passes(self):
        text = "Code committed. Release pending CI and artifact upload."
        assert not is_false_done(text), (
            "A release claim with 'pending' hedge MUST pass."
        )

    def test_blocked_release_passes(self):
        text = "Shipped locally, blocked on CI to publish the GitHub Release."
        assert not is_false_done(text), (
            "A shipped claim with 'blocked' hedge MUST pass."
        )

    def test_unverified_release_passes(self):
        text = "Release is unverified — artifact not yet confirmed."
        assert not is_false_done(text), (
            "'unverified' is an honest hedge — MUST pass."
        )

    def test_not_shipped_passes(self):
        text = "The release is not shipped yet."
        assert not is_false_done(text), (
            "'not shipped' is an honest hedge — MUST pass."
        )


class TestNonReleaseClaimWithGeneralEvidence:
    """A non-release claim (done, fixed, landed) with general evidence passes
    — the release-specific evidence gate only applies to shipped/released/deployed."""

    def test_done_with_commit_sha_passes(self):
        text = "Done. Commit abc1234 — feature wired up."
        assert not is_false_done(text), (
            "A 'done' claim (not a release claim) with a commit SHA MUST pass."
        )

    def test_fixed_with_gate_pass_passes(self):
        text = "Bug fixed. .gate-status PASS, 50 passed."
        assert not is_false_done(text), (
            "A 'fixed' claim (not a release claim) with gate evidence MUST pass."
        )

    def test_landed_with_ci_verdict_passes(self):
        text = "Landed. make ci-verdict BRANCH=master → conclusion: success."
        assert not is_false_done(text), (
            "A 'landed' claim (not a release claim) with ci-verdict MUST pass."
        )

    def test_complete_with_pass_count_passes(self):
        text = "Complete. 5000 passed."
        assert not is_false_done(text), (
            "A 'complete' claim (not a release claim) with a pass count MUST pass."
        )


class TestPluginSourceInSync:
    """The merged enforce-stop.ts contains the false-done guardrail constants
    (COMPLETION_VERBATIM, DIRECT_FALSE_DONE_FLAGS, etc.).
    These MUST stay in sync with this test's Python port."""

    def _src(self):
        plugin = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
        return plugin_contract_source(plugin)

    def test_completion_verbatim_exists(self):
        src = self._src()
        assert "COMPLETION_VERBATIM" in src, (
            "COMPLETION_VERBATIM must be defined in enforce-stop.ts (merged false-done guardrail)."
        )

    def test_false_done_blocks_file_exists(self):
        src = self._src()
        assert "FALSE_DONE_BLOCKS_FILE" in src, (
            "FALSE_DONE_BLOCKS_FILE must be defined in enforce-stop.ts."
        )

    def test_commit_hash_re_exists(self):
        src = self._src()
        assert "COMMIT_HASH_RE" in src, (
            "COMMIT_HASH_RE (commit evidence pattern) must be defined in enforce-stop.ts."
        )

    def test_log_false_done_block_exists(self):
        src = self._src()
        assert "logFalseDoneBlock" in src, (
            "logFalseDoneBlock function must be defined in enforce-stop.ts "
            "for auditing blocked false-done claims."
        )
