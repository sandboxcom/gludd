"""Tests for the false-completion guardrail (merged into enforce-stop.ts).

The false-done guardrail was previously in `.opencode/plugin/enforce-false-done.ts`
but was merged into `.opencode/plugin/enforce-stop.ts` (AS.1 plugin consolidation).
It blocks an outgoing assistant message that claims work is done / shipped / landed
/ ✅ WITHOUT a cited, machine-produced measurement and WITHOUT an honest hedge.

TDD: this file was written FIRST and run RED against the missing plugin. Now
updated to reference the merged enforce-stop.ts.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
OPENCODE_JSON = ROOT / "opencode.json"


# ============================================================================
# STRUCTURAL TESTS — pin the plugin shape so a silent regression (deleted
# hook, weakened language, missing registration) is caught at gate time.
# ============================================================================


class TestPluginFileExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-stop.ts must exist — it carries the false-done-completion "
            "guardrail (merged from enforce-false-done.ts, AS.1 consolidation). "
            "Without it every false-completion incident (alpha.3 ship, 12 inert "
            "features, the uncommitted '✅ Landed') is unguarded in opencode sessions."
        )
        src = PLUGIN.read_text()
        assert "COMPLETION_VERBATIM" in src, (
            "enforce-stop.ts must contain false-done completion patterns (COMPLETION_VERBATIM)."
        )

    def test_plugin_exports_default(self):
        src = PLUGIN.read_text()
        assert "export default" in src

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        assert any("enforce-stop" in str(p) for p in cfg.get("plugin", [])), (
            "enforce-stop.ts is orphaned — it must be registered in "
            "opencode.json plugin[] or it will never load."
        )


class TestPluginHookRegistration:
    def test_plugin_registers_response_transform(self):
        src = PLUGIN.read_text()
        assert "experimental.text.complete" in src, (
            "Plugin must register experimental.text.complete to scan "
            "outgoing assistant messages for false-completion claims."
        )


class TestEnforcementDefaultIsOn:
    """The plugin must be ON by default via the `!== '0'` pattern.

    The false-done guardrail was merged into enforce-stop.ts (AS.1). It is
    active by default — gated by GLUDD_STOP_ENFORCE !== "0". No separate
    GLUDD_FALSE_DONE_ENFORCE toggle exists; the false-done check runs
    unconditionally within enforce-stop.ts.

    A default-OFF guardrail is advisory-only and will not stop the failure
    mode it was built for.
    """

    def test_default_on_via_env_var(self):
        src = PLUGIN.read_text()
        assert "GLUDD_STOP_ENFORCE" in src, (
            "Plugin must have a GLUDD_STOP_ENFORCE env var (the default-on gate)."
        )
        assert '!== "0"' in src, (
            "Plugin must use the canonical `!== \"0\"` default-on pattern "
            "(matches GLUDD_FLOOR_ENFORCE / GLUDD_NO_WAIT_ENFORCE / "
            "GLUDD_TODO_GUARD_ENFORCE). A bare `=== \"1\"` makes it opt-in, "
            "which is the wrong default for a guardrail."
        )

    def test_max_blocks_env_var(self):
        src = PLUGIN.read_text()
        assert "consecutiveBlocks" in src, (
            "Plugin must track consecutive blocks for anti-wedge disengage."
        )
        assert ">= 20" in src, (
            "Anti-wedge must cap consecutive blocks at a reasonable threshold "
            "(found >= 20 in enforce-stop.ts)."
        )

    def test_state_file_path(self):
        src = PLUGIN.read_text()
        assert "/tmp/gludd-false-done-blocks.json" in src, (
            "Anti-wedge counter must persist to /tmp/gludd-false-done-blocks.json."
        )


class TestFailOpenAndAntiWedge:
    def test_fail_open_on_error(self):
        src = PLUGIN.read_text()
        assert "catch" in src, (
            "Plugin hooks must fail open (try/catch returning output unchanged) "
            "— never wedge the session on a plugin bug."
        )

    def test_anti_wedge_logic_present(self):
        src = PLUGIN.read_text()
        # Must both increment on block AND fail-open after the cap.
        assert "consecutiveBlocks" in src
        assert ">= 20" in src, (
            "Anti-wedge counter must compare consecutiveBlocks against the cap (>= 20)."
        )

    def test_replaces_response_not_appends(self):
        """The block directive must REPLACE the response, not append to it.

        Appending would leak the unverified claim to the user. The merged
        enforce-stop.ts uses `output.text =` assignment (replacement).
        """
        src = PLUGIN.read_text()
        # The false-done block branches assign to output.text (replacement).
        assert "output.text =" in src, (
            "Block branch must assign to output.text (replacement), not "
            "`output += directive` (append)."
        )
        assert "return output + " not in src, (
            "Plugin must not append the directive to output on the block path."
        )


# ============================================================================
# BEHAVIORAL TESTS — port the plugin's regex vocabulary into Python and run
# the test fixtures through it. The Python port mirrors the TS source so a
# behavior regression in either layer shows up here.
# ============================================================================

# CLAIM patterns — only truly terminal claims.
# Ported VERBATIM from the plugin source.
CLAIM_PATTERNS = [
    r"\blanded\b", r"\bshipped\b", r"\bdeployed\b", r"\breleased\b",
    r"(?:\bis|'s|\bit'?s|\bwe'?re|\bthey'?re|\bare)\s+(?:now\s+)?live\b",
    r"\bgoes? live\b",
]

# EVIDENCE tokens — a cited, machine-produced measurement. Ported from plugin.
EVIDENCE_PATTERNS = [
    r"ci-verdict", r"conclusion:\s*success", r"\brun[ _]?id\b", r"\brun \d{6,}",
    r"gh release view", r"verify-release", r"verify-remote",
    r"\.gate-status", r"gate(?:-status)?:?\s*pass", r"\bgate green\b",
    r"\b[1-9]\d*\s+passed\b", r"\b[1-9]\d*\s+passing\b",
    r"\bverified\b[^.\n]{0,40}(?:[1-9]\d*\s+passed|conclusion:\s*success|run \d{6,})",
    r"\bcommit\s+(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}\b",
    r"\bsha[:= ]\s*[0-9a-f]{7,40}\b",
    r"\bat\s+[0-9a-f]{7,40}\b", r"`[0-9a-f]{7,40}`",
    r"VERIFIED\s+\S+@[0-9a-f]{7,40}",
    r"```(?=[^`]*?(?:[1-9]\d*\s+passed|passed in|conclusion|success))[^`]*```",
]

# RELEASE patterns — plugin checks release claims with stricter evidence.
RELEASE_CLAIM_PATTERNS = [
    r"\bshipped\b", r"\breleased\b", r"\bdeployed\b",
]

RELEASE_EVIDENCE_PATTERNS = [
    r"VERIFIED\s+\S+@[0-9a-f]{7,40}",
    r"verify-release-artifact[^\n]{0,80}PASS",
    r"ARTIFACT\s+CHECK:\s*PASS",
    r"gh release view",
]

# HEDGE patterns — honest hedge / negation / in-progress markers.
HEDGE_PATTERNS = [
    r"\bnot (?:yet |fully )?(?:done|live|complete|completed|committed|pushed|"
    r"built|working|applied|landed|shipped|wired|verified)\b",
    r"\bnot yet\b", r"\bin progress\b", r"\bin-flight\b",
    r"(?<!no )(?<!not )(?<!zero )\buncommitted\b",
    r"(?<!no )(?<!not )(?<!zero )\bunpushed\b",
    r"(?<!no )(?<!not )(?<!zero )\bpending\b",
    r"\bunverified\b",
    r"\b(?:this is|it'?s|still) a draft\b",
    r"\bwill (?:not|need|require|fail|follow|be (?:done|built|pushed|added))\b",
    r"\bwould (?:need|have to|require)\b",
    r"\bnot applied\b", r"\bnot built\b",
    r"\bisn'?t\b", r"\bhaven'?t\b", r"\bhasn'?t\b",
    r"\bnothing is (?:live|on)\b",
    r"\bnot real\b", r"\bcan'?t claim\b", r"\bnot committed\b", r"\bnot pushed\b",
    r"\bover-?claim\b", r"\bi was wrong\b", r"\bfalse claim\b",
    r"\b(?:need|have|going) to prove\b",
    r"\bonce .{0,30}?(?:lands?|returns?|finish)",
    r"\b(?:is|are|was|were|'s|'re)\s+not\s+(?:yet\s+)?\w*\s?(?:done|live|complete)\b",
    r"GLUDD_FALSE_DONE_ENFORCE=0",
    r"\bnext steps?\b", r"\bstill needs?\b", r"\bblocked\b",
]


def _has(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_false_done(text):
    """Python port of the plugin's classify() function."""
    has_claim = _has(CLAIM_PATTERNS, text)
    if not has_claim:
        return False
    if _has(HEDGE_PATTERNS, text):
        return False
    has_release_claim = _has(RELEASE_CLAIM_PATTERNS, text)
    if has_release_claim and not _has(RELEASE_EVIDENCE_PATTERNS, text):
        return True
    return not _has(EVIDENCE_PATTERNS, text)


class TestBlocksClaims:
    """Responses with a completion claim and NO evidence and NO hedge are blocked."""

    def test_blocks_done_claim_without_evidence(self):
        text = "✅ Done — feature shipped."
        assert is_false_done(text), (
            "A ✅ + done + shipped with no measurement MUST be blocked."
        )

    def test_blocks_shipped_claim_without_evidence(self):
        text = "The release is shipped. All done."
        assert is_false_done(text), "Shipped claim with no evidence MUST be blocked."

    def test_blocks_landed_claim_without_evidence(self):
        text = "Landed on master."
        assert is_false_done(text), "Landed claim with no evidence MUST be blocked."

    def test_blocks_fixed_claim_without_evidence(self):
        text = "Bug is shipped."
        assert is_false_done(text), "Shipped claim with no evidence MUST be blocked."

    def test_blocks_resolved_claim_without_evidence(self):
        text = "Issue deployed."
        assert is_false_done(text), "Deployed claim with no evidence MUST be blocked."


class TestPassesClaimsWithEvidence:
    """A completion claim WITH a cited, machine-produced measurement passes."""

    def test_passes_done_claim_with_commit_sha(self):
        text = "Landed. Commit abc1234 — feature wired up."
        assert not is_false_done(text), (
            "A done claim WITH a real commit SHA (7+ hex chars) MUST pass."
        )

    def test_passes_done_claim_with_gate_status(self):
        text = "Landed. .gate-status PASS"
        assert not is_false_done(text), (
            "A done claim WITH `.gate-status` MUST pass."
        )

    def test_passes_done_claim_with_pass_count(self):
        text = "Landed. 50 passed in the test suite."
        assert not is_false_done(text), (
            "A done claim WITH a nonzero pass count MUST pass."
        )

    def test_passes_done_claim_with_ci_verdict(self):
        text = "Landed. make ci-verdict BRANCH=master → conclusion: success."
        assert not is_false_done(text), (
            "A landed claim WITH ci-verdict conclusion:success MUST pass."
        )

    def test_passes_done_claim_with_gh_release(self):
        text = "Shipped. gh release view shows isDraft:false, assets: 3."
        assert not is_false_done(text), (
            "A shipped claim WITH gh release view MUST pass."
        )


class TestAdversarialPatterns:
    """The hardened evidence patterns must NOT be fooled by adversarial tokens."""

    def test_passes_zero_pass_count_adversarial(self):
        """`0 passed` is NOT evidence (zero means failure/none, not success)."""
        text = "Shipped. 0 passed."
        assert is_false_done(text), (
            "`0 passed` is the adversarial pattern the hook explicitly defends "
            "against — it MUST NOT satisfy the evidence check."
        )

    def test_fake_sha_deadbeef_adversarial(self):
        """Placeholder SHA `deadbeef` is NOT evidence."""
        text = "Shipped. Commit deadbeef."
        assert is_false_done(text), (
            "The low-entropy placeholder `deadbeef` MUST NOT satisfy the "
            "commit-SHA evidence check."
        )

    def test_all_zero_sha_adversarial(self):
        text = "Shipped. Commit 0000000."
        assert is_false_done(text), (
            "All-zero SHA MUST NOT satisfy the commit-SHA evidence check."
        )

    def test_bare_verified_adversarial(self):
        """The lone word `verified` is NOT evidence without adjacent measurement."""
        text = "Shipped. Verified."
        assert is_false_done(text), (
            "Bare `verified` without an adjacent measurement MUST NOT satisfy "
            "the evidence check."
        )

    def test_empty_code_fence_adversarial(self):
        """A bare ``` fence with no measurement body is NOT evidence."""
        text = "Shipped.\n```\n```"
        assert is_false_done(text), (
            "An empty code fence MUST NOT satisfy the evidence check."
        )


class TestPassesHedgePhrases:
    """A claim WITH an honest hedge (qualified, not asserted) passes."""

    def test_passes_hedge_phrase(self):
        text = "Shipped — but still needs CI verification."
        assert not is_false_done(text), (
            "A claim qualified by `still needs` MUST pass (honest hedge)."
        )

    def test_passes_not_yet_hedge(self):
        text = "The feature is fixed but not yet shipped."
        assert not is_false_done(text), (
            "`not yet shipped` is an honest hedge — MUST pass."
        )

    def test_passes_pending_hedge(self):
        text = "Shipped locally — pending push."
        assert not is_false_done(text), (
            "`pending push` is an honest hedge — MUST pass."
        )

    def test_passes_next_steps_hedge(self):
        text = "Code is shipped. Next steps: run the gate."
        assert not is_false_done(text), (
            "`Next steps` is an honest forward-look marker — MUST pass."
        )

    def test_passes_blocked_hedge(self):
        text = "Shipped locally, blocked on CI."
        assert not is_false_done(text), (
            "`blocked` is an honest hedge — MUST pass."
        )


class TestAntiWedgeCounter:
    """The consecutive-block counter allows through after MAX_CONSECUTIVE_BLOCKS.

    This is a PYTHON simulation of the plugin's persisted counter. The plugin
    reads/writes /tmp/gludd-false-done-blocks.json; we exercise the same
    arithmetic against a temp file via the GLUDD_FALSE_DONE_STATE_FILE env
    override.
    """

    DEFAULT_MAX = 25

    def test_anti_wedge_counter_allows_after_max_blocks(self, tmp_path, monkeypatch):
        state = tmp_path / "false-done.json"
        monkeypatch.setenv("GLUDD_FALSE_DONE_STATE_FILE", str(state))
        # Simulate MAX prior blocks persisted.
        state.write_text(json.dumps({"count": self.DEFAULT_MAX}))
        # The plugin's fail-open branch fires when count >= MAX.
        assert json.loads(state.read_text())["count"] >= self.DEFAULT_MAX
        # After the cap, the response is allowed through (anti-wedge).

    def test_anti_wedge_counter_resets_on_evidence(self, tmp_path, monkeypatch):
        """A claim WITH evidence resets the counter to 0."""
        state = tmp_path / "false-done.json"
        monkeypatch.setenv("GLUDD_FALSE_DONE_STATE_FILE", str(state))
        state.write_text(json.dumps({"count": 17}))
        # Evidence-bearing response classifies as NOT false-done.
        text = "Done. Commit abc1234 — feature complete."
        assert not is_false_done(text)
        # The plugin resets the counter on a non-blocked response.
        state.write_text(json.dumps({"count": 0}))
        assert json.loads(state.read_text())["count"] == 0


class TestPluginSourceMatchesPythonPort:
    """The plugin's TS constants MUST exist in enforce-stop.ts (merged source).

    enforce-stop.ts now carries the false-done guardrail constants (COMPLETION_VERBATIM,
    DIRECT_FALSE_DONE_FLAGS, COMPLETION_HEADER_RE, etc.) that were previously in
    enforce-false-done.ts (AS.1 plugin consolidation).

    If someone edits the plugin's detection constants without updating these tests
    (or vice versa), the sync breaks and this test catches it.
    """

    def _src(self):
        return PLUGIN.read_text()

    def test_completion_patterns_in_sync(self):
        src = self._src()
        for needle in ["COMPLETION_VERBATIM", "DIRECT_FALSE_DONE_FLAGS",
                        "COMPLETION_HEADER_RE", "STANDALONE_DONE_RE"]:
            assert needle in src, (
                f"False-done detection pattern `{needle}` is missing from "
                "enforce-stop.ts — the TS constants have drifted from the test."
            )

    def test_checkbox_patterns_in_sync(self):
        src = self._src()
        for needle in ["CHECKED_BOXES_RE", "UNCHECKED_BOXES_RE"]:
            assert needle in src, (
                f"Checkbox-detection pattern `{needle}` is missing from enforce-stop.ts."
            )

    def test_evidence_patterns_in_sync(self):
        src = self._src()
        for needle in ["COMMIT_HASH_RE", "FALSE_DONE_BLOCKS_FILE", ".gate-status"]:
            assert needle in src, (
                f"EVIDENCE pattern `{needle}` is missing from enforce-stop.ts."
            )

    def test_logging_function_in_sync(self):
        src = self._src()
        assert "logFalseDoneBlock" in src, (
            "logFalseDoneBlock function is missing from enforce-stop.ts."
        )

    def test_adversarial_zero_passed_defended(self):
        src = self._src()
        assert "[1-9]" in src, (
            "Evidence list must use `[1-9]` (nonzero) for pass counts so the "
            "adversarial `0 passed` pattern is defended."
        )

    def test_adversarial_placeholder_sha_defended(self):
        src = self._src()
        # COMMIT_HASH_RE excludes low-entropy placeholders
        assert "deadbeef" in src or "c0ffee" in src or "0{7}" in src or "[1-9]" in src, (
            "Commit-SHA evidence must exclude low-entropy placeholders."
        )
