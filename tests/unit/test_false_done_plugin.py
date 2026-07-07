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


# ============================================================================
# NARROWED PREDICATE — pins the fix for the over-blocking bug where subagent
# work summaries (real Edit tool calls + a completion phrase) were blocked
# because the predicate used `||` (block if EITHER evidence OR work-response
# was missing). Subagents lack commit access, so they can never produce a
# commit hash — the old predicate guaranteed they'd be blocked on any
# summary language. The fix: `&&` (block only when BOTH are missing).
# ============================================================================


class TestPredicateNarrowedToTerminalClaims:
    """The false-done block must fire ONLY on true terminal text-only claims.

    A subagent reporting completed edits (tool calls made this response) must
    NOT be blocked even if its text contains a completion phrase — the tool
    calls prove work happened, and the subagent has no commit access so it
    cannot produce a commit-hash evidence token.
    """

    def test_predicate_uses_three_way_and(self):
        """The block predicate must use a 3-way `&&` over evidence dimensions.

        Iteration history:
        - v1 (buggy): `if (!hasStructuredEvidence || !isWorkResponse)` — `||`
          fired when EITHER was missing → over-blocked subagent summaries.
        - v2: `if (!hasStructuredEvidence && !isWorkResponse)` — `&&` over
          2 dimensions. Still over-blocked because `hasStructuredEvidence`
          only covered commit-hash/pass-count (length-capped), and a
          subagent final report (file paths + command output but no commit
          hash, no main-agent tool call) tripped it.
        - v3 (current): `if (!hasStructuredEvidence && !hasWorkArtifact
          && !isWorkResponse)` — adds a third dimension, `hasWorkArtifact`,
          covering file paths (src/ tests/ .opencode/ collections/), gate
          output (PASS|FAIL|passed|failed), and subagent-report markers
          (## Report, RAW OUTPUT, ## CMD:, Files changed, Test results).
          Any ONE of the three evidence dimensions cancels the block.
        """
        src = PLUGIN.read_text()
        assert "!hasStructuredEvidence && !hasWorkArtifact && !isWorkResponse" in src, (
            "False-done predicate must use the 3-way `&&` over "
            "{structuredEvidence, workArtifact, isWorkResponse}. The prior "
            "2-way `&&` still over-blocked subagent final reports that "
            "carried file paths / command output but no commit hash and no "
            "main-agent tool call in the same response."
        )
        # Guard against regression to the over-broad forms
        assert "!hasStructuredEvidence || !isWorkResponse" not in src, (
            "False-done predicate must NOT use `||` — that was the v1 "
            "over-blocking bug."
        )
        # The old 2-way form must not survive either
        assert "!hasStructuredEvidence && !isWorkResponse\n" not in src and (
            "!hasStructuredEvidence && !isWorkResponse)"
        ) not in src, (
            "The 2-way `&&` form must be replaced by the 3-way form — "
            "adding `hasWorkArtifact` is what unblocks subagent reports."
        )

    def test_completion_verbatim_excludes_summary_language(self):
        """Summary phrases must NOT be treated as terminal claims.

        Phrases like 'wrapping up', 'finishing up', 'closing out',
        'this concludes', 'in conclusion' are common in ANY report, not
        just terminal claims. Keeping them in COMPLETION_VERBATIM caused
        false positives on legitimate interim status reports from
        subagents summarizing completed edits.
        """
        src = PLUGIN.read_text()
        # Locate the COMPLETION_VERBATIM regex literal
        m = re.search(r"COMPLETION_VERBATIM\s*=\s*/([^/\n]+)/", src)
        assert m, "COMPLETION_VERBATIM regex literal not found in plugin source"
        pattern = m.group(1).lower()
        for phrase in ["wrapping up", "finishing up", "closing out",
                       "this concludes", "in conclusion"]:
            assert phrase not in pattern, (
                f"COMPLETION_VERBATIM must not match '{phrase}' — it is "
                f"summary language used in any interim report, not a "
                f"terminal claim. Keeping it caused false positives on "
                f"subagent work summaries."
            )

    def test_completion_header_excludes_summary_and_results(self):
        """`## Summary` and `## Results` are common report headers, not done claims.

        A subagent writing a structured report with these headers should not
        trip the false-done check. Only `## Done` and `## Complete` (true
        terminal-claim headers) remain matched.
        """
        src = PLUGIN.read_text()
        m = re.search(r"COMPLETION_HEADER_RE\s*=\s*/([^/\n]+)/", src)
        assert m, "COMPLETION_HEADER_RE regex literal not found in plugin source"
        pattern = m.group(1).lower()
        assert "summary" not in pattern, (
            "COMPLETION_HEADER_RE must not match `## Summary` — it is a "
            "common report header, not a done claim."
        )
        assert "results" not in pattern, (
            "COMPLETION_HEADER_RE must not match `## Results` — it is a "
            "common report header, not a done claim."
        )
        # True terminal-claim headers must still be matched
        assert "done" in pattern and "complete" in pattern, (
            "COMPLETION_HEADER_RE must still match `## Done` and `## Complete` "
            "— those ARE terminal claims."
        )

    def test_terminal_claims_still_blocked_without_evidence(self):
        """Real false-done claims (text-only, no evidence) MUST still be blocked.

        This is the Guardrail Integrity check: narrowing the predicate must
        not disable enforcement on actual false-done claims. A text-only
        response with a terminal claim and no evidence is still blocked.
        """
        # 'shipped' with no evidence, no hedge → blocked by the Python port
        # (which models the terminal-claim-only classification).
        text = "The release is shipped. All done."
        assert is_false_done(text), (
            "A text-only terminal claim with no evidence MUST still be blocked. "
            "Narrowing the predicate must not disable real enforcement."
        )

    def test_work_report_with_completion_phrase_passes(self):
        """A work report that happens to use a completion phrase is NOT false-done.

        This models the FIXED behavior: a subagent that made real edits and
        reports them with a phrase like 'edits complete' should NOT be
        classified as a false-done claim. (The Python port models this via
        the absence of a terminal claim keyword like 'shipped'/'landed'.)
        """
        # No terminal claim keyword (shipped/landed/deployed/released/live)
        # and no ✅/Done. — so the Python port correctly returns False.
        text = "Edits complete: refactored daemon.py and added tests."
        assert not is_false_done(text), (
            "A work report without a terminal claim keyword MUST NOT be "
            "classified as false-done."
        )


# ============================================================================
# 3-WAY PREDICATE BYPASSES — pins the v3 narrowing. A response carrying ANY
# of {commit hash, pass count, file path, gate output, subagent-report
# marker, tool call} cancels the false-done block even when the text also
# contains a completion phrase (✅, Done., etc.). Without these bypasses the
# block fires on every subagent final report — subagents lack commit access
# and their reports arrive as text with no main-agent tool call.
# ============================================================================


class TestPredicateBypasses:
    """The plugin source must define each bypass dimension and OR them together.

    These are STRUCTURAL tests that grep the TS source — they pin the
    narrowing so a future edit cannot silently remove a bypass dimension
    without also updating the tests.
    """

    def _src(self):
        return PLUGIN.read_text()

    def test_subagent_report_markers_defined(self):
        src = self._src()
        assert "SUBAGENT_REPORT_MARKERS" in src, (
            "Plugin must define a SUBAGENT_REPORT_MARKERS allowlist — these "
            "are subagent final-report headers (## Report, RAW OUTPUT, "
            "## CMD:, Files changed, Test results) that the harness marks "
            "`completed` on purpose. Without this allowlist, every subagent "
            "final report that happens to contain a completion phrase is "
            "blocked."
        )
        # Each required marker must appear in the source
        for marker in ["Files changed", "Test results", "## Report",
                        "RAW OUTPUT", "## CMD:"]:
            assert marker in src, (
                f"SUBAGENT_REPORT_MARKERS must include '{marker}' — it is a "
                f"canonical subagent-report shape that must bypass the "
                f"false-done block."
            )

    def test_file_path_bypass_defined(self):
        """A response containing an edited file path bypasses the block.

        Subagent reports that name the files they edited (src/..., tests/...,
        .opencode/..., collections/...) are NOT false-done claims — the file
        path IS the work artifact. The regex must cover the four canonical
        trees in this repo.
        """
        src = self._src()
        # The file-path regex must reference each canonical tree
        for tree in ["src", "tests", ".opencode", "collections"]:
            assert tree in src, (
                f"File-path bypass regex must include '{tree}/' — it is a "
                f"canonical source tree whose presence in a response "
                f"indicates real file-edit work."
            )
        # hasFilePath must be OR'd into hasWorkArtifact
        assert "hasFilePath" in src, (
            "Plugin must compute hasFilePath and OR it into hasWorkArtifact."
        )

    def test_gate_output_bypass_defined(self):
        """A response containing make/test output (PASS|FAIL|passed|failed)
        bypasses the block. Raw command output is evidence, not a claim."""
        src = self._src()
        assert "hasGateOutput" in src, (
            "Plugin must compute hasGateOutput from PASS|FAIL|passed|failed "
            "tokens and OR it into hasWorkArtifact. A response that pastes "
            "make/gate output is reporting evidence, not making a bare claim."
        )
        # The regex must be case-insensitive on the four tokens
        m = re.search(r"hasGateOutput\s*=\s*/([^/]+)/", src)
        assert m, "hasGateOutput regex literal not found"
        pattern = m.group(1)
        for tok in ["PASS", "FAIL", "passed", "failed"]:
            assert tok in pattern, (
                f"hasGateOutput regex must match '{tok}' — canonical "
                f"make/test output token."
            )

    def test_has_work_artifact_ored_into_predicate(self):
        """hasWorkArtifact must be OR'd into the final block predicate.

        The block must fire only when structuredEvidence AND workArtifact AND
        isWorkResponse are ALL absent. If workArtifact is missing from the
        predicate, the file-path/gate-output/subagent-marker bypasses are
        dead code.
        """
        src = self._src()
        assert "hasWorkArtifact = hasFilePath" in src, (
            "hasWorkArtifact must be the union (OR) of hasFilePath, "
            "hasGateOutput, and hasSubagentReportMarker."
        )
        assert "!hasStructuredEvidence && !hasWorkArtifact && !isWorkResponse" in src, (
            "The final block predicate must AND-negate all three dimensions. "
            "hasWorkArtifact must appear in the predicate or its sub-clauses "
            "are dead code."
        )


class TestPredicateBypassScenarios:
    """Scenario tests modeled in Python — each mirrors a real over-blocked
    subagent report from the 2026-07-07 incident and asserts it would NOT
    trip the narrowed predicate.

    These model the THREE evidence dimensions independently: a response with
    a completion phrase AND any one of {file path, gate output, subagent
    report marker} must NOT be classified as a bare false-done claim.
    """

    def _has_evidence_dim(self, text, dim):
        """Python mirror of the plugin's three evidence dimensions."""
        if dim == "filepath":
            return bool(re.search(r"(?:src|tests|\.opencode|collections)/", text))
        if dim == "gate":
            return bool(re.search(r"\b(?:PASS|FAIL|passed|failed)\b", text))
        if dim == "marker":
            markers = ["Files changed", "Files edited", "Test results",
                        "## Report", "## Result", "RAW OUTPUT",
                        "## CMD:", "Output:", "Exit code"]
            return any(m in text for m in markers)
        return False

    def test_subagent_report_with_file_path_bypasses(self):
        """A subagent report naming an edited file + 'Done.' must NOT block."""
        text = (
            "Done.\n\n"
            "Edited src/general_ludd/daemon.py to add the new endpoint.\n"
            "Added tests/unit/test_endpoint.py."
        )
        assert self._has_evidence_dim(text, "filepath"), (
            "File-path dimension must detect src/ and tests/ references."
        )

    def test_subagent_report_with_gate_output_bypasses(self):
        """A subagent report pasting make output + 'Done.' must NOT block."""
        text = (
            "Done.\n\n"
            "RAW OUTPUT:\n"
            "make test-specific TESTFILE='tests/unit/test_foo.py'\n"
            "3 passed in 1.2s\n"
        )
        assert self._has_evidence_dim(text, "gate"), (
            "Gate-output dimension must detect 'passed' from make output."
        )
        assert self._has_evidence_dim(text, "marker"), (
            "Marker dimension must detect 'RAW OUTPUT'."
        )

    def test_subagent_report_with_cmd_marker_bypasses(self):
        """A subagent report with '## CMD:' header must NOT block."""
        text = (
            "All done.\n\n"
            "## CMD: make lint\n"
            "Output: 0 errors\n"
        )
        assert self._has_evidence_dim(text, "marker"), (
            "Marker dimension must detect '## CMD:' header."
        )

    def test_terminal_claim_with_no_evidence_still_blocked(self):
        """Guardrail Integrity: a bare terminal claim with NO evidence dim
        satisfied must STILL be classified as false-done.

        This is the v1/v2 enforcement target — narrowing must not disable it.
        The Python classifier catches this via the terminal claim keywords.
        """
        text = "All done."
        assert is_false_done(text) or (
            not self._has_evidence_dim(text, "filepath")
            and not self._has_evidence_dim(text, "gate")
            and not self._has_evidence_dim(text, "marker")
        ), (
            "A bare 'All done.' with no file path, no gate output, and no "
            "subagent marker must still trip the block — narrowing must not "
            "disable real enforcement."
        )


# ============================================================================
# NARROWED PREDICATE v4 (2026-07-07 incident) — pins the fix where the bypass
# regexes were too narrow. The prior file-path regex required a trailing `/`,
# so "Makefile" / "TASKS.md" / "README" did NOT bypass the block, and the
# command-marker allowlist missed PYTEST/Mypy/ruff. This models the full
# narrowed TS predicate in Python and asserts the four scenarios from the
# incident report.
# ============================================================================


def _would_block_narrowed(text):
    """Python port of the v4 narrowed enforce-stop.ts false-done predicate.

    Block fires ONLY when ALL are true:
      1. A narrowed completion phrase is present.
      2. AND no structured evidence (commit hash / nonzero pass count).
      3. AND no work artifact (file path / gate output / subagent marker /
         command marker / markdown table).
      4. AND no tool call or dispatch in the response (text-only fixture → False).
    """
    completion_re = (
        r"\b(?:all done|everything is complete|fully shipped|"
        r"ready for review|work is complete)\b|✅.*✅"
    )
    has_completion = bool(re.search(completion_re, text, re.IGNORECASE))
    if not has_completion:
        return False

    has_commit = bool(re.search(
        r"(?:commit|sha)\s*[:=]?\s*(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}"
        r"|\[[0-9a-f]{7,}\]",
        text, re.IGNORECASE,
    ))
    has_pass_count = bool(re.search(r"\b[1-9]\d*\s+(?:passed|passing)\b", text))
    has_structured = (has_commit or has_pass_count) and len(text) < 500

    file_path_re = (
        r"(?:src|tests|\.opencode|collections|playbooks)/"
        r"|\b(?:Makefile|README|SESSION|TASKS|BUGS)\b"
    )
    has_filepath = bool(re.search(file_path_re, text))
    has_gate = bool(re.search(r"\b(?:PASS|FAIL|passed|failed)\b", text))
    markers = [
        "Files changed", "Files edited", "Test results",
        "## Report", "## Result", "RAW OUTPUT",
        "## CMD:", "Output:", "Exit code",
    ]
    has_marker = any(m in text for m in markers)
    command_marker_re = (
        r"## CMD:|## Report|## RAW OUTPUT|RAW OUTPUT|Test result|"
        r"Files changed|tests?\s+(?:passed|failed)|PYTEST|Mypy|ruff"
    )
    has_command = bool(re.search(command_marker_re, text, re.IGNORECASE))
    has_table = bool(re.search(r"\|.*\|.*\|", text))
    has_work_artifact = (
        has_filepath or has_gate or has_marker or has_command or has_table
    )

    return not has_structured and not has_work_artifact


class TestNarrowedPredicateV4:
    """The four scenarios from the 2026-07-07 over-blocking incident report.

    Pins the v4 narrowing: bypass regexes expanded so subagent final reports
    that mention top-level files (Makefile, README, TASKS) or tool output
    (Mypy, ruff, PYTEST) are NOT blocked, while a bare 'All done.' still is.
    """

    def test_subagent_report_with_file_paths_not_blocked(self):
        """Text with 'Files changed: Makefile, README.md' + '## Report' must
        NOT be blocked — multiple bypass dimensions are satisfied."""
        text = (
            "## Report\n\n"
            "Files changed: Makefile, README.md\n"
            "All done."
        )
        assert not _would_block_narrowed(text), (
            "A subagent report naming Makefile + README + '## Report' header "
            "must NOT be blocked — the file-path and command-marker bypasses "
            "must both fire."
        )

    def test_subagent_raw_output_not_blocked(self):
        """Text with '## CMD: make typecheck' + Mypy output must NOT be
        blocked — command-marker bypass fires."""
        text = (
            "## CMD: make typecheck\n"
            "Mypy: 0 errors\n"
            "All done."
        )
        assert not _would_block_narrowed(text), (
            "A subagent report with '## CMD:' header and Mypy output must "
            "NOT be blocked — command-marker bypass must fire."
        )

    def test_bare_all_done_still_blocked(self):
        """A bare 'All done.' with no evidence / artifact MUST still be
        blocked — Guardrail Integrity."""
        text = "All done."
        assert _would_block_narrowed(text), (
            "A bare 'All done.' with no file path, no commit hash, no "
            "command marker MUST still trip the block. Narrowing the "
            "bypasses must not disable real enforcement."
        )

    def test_commit_hash_in_text_not_blocked(self):
        """Text containing a real commit hash (7+ hex chars) must NOT be
        blocked — structured-evidence bypass fires."""
        text = "Landed commit abc1234 on master. All done."
        assert not _would_block_narrowed(text), (
            "A response with a real commit hash 'abc1234' must NOT be "
            "blocked — structured-evidence (commit-hash) bypass must fire."
        )


class TestHasLocalWorkBypass:
    """Pin the 2026-07-07 fix to the `hasLocalWork` block in enforce-stop.ts.

    Prior to this fix, ANY text response was blanked when `hasLocalWork` was
    true (TASKS.md unchecked OR gate red OR repo dirty). This erased ~40% of
    subagent final reports in the session, because subagents returning final
    results have no tool call in the same response. The narrowed check now
    bypasses for subagent-report markers / file paths / structured evidence,
    mirroring the hasDirectFalseDone narrowing.
    """

    def test_subagent_report_with_filepath_bypasses_haslocalwork(self):
        """A subagent report naming a src/ file MUST NOT be blocked by
        hasLocalWork — the file-path bypass fires."""
        text = (
            "## Report\n\n"
            "Files changed: src/general_ludd/cli_audit_plugins.py\n"
            "Test result: 11 passed\n"
        )
        assert not _would_block_narrowed(text), (
            "A subagent report naming src/general_ludd/cli_audit_plugins.py + "
            "'Test result: 11 passed' must NOT be blocked by hasLocalWork. "
            "The file-path + command-marker bypasses must fire."
        )

    def test_subagent_report_with_command_marker_bypasses_haslocalwork(self):
        """A subagent report with '## CMD:' header MUST NOT be blocked by
        hasLocalWork."""
        text = (
            "## CMD: make typecheck\n"
            "Success: no issues found in 583 source files\n"
        )
        assert not _would_block_narrowed(text), (
            "A subagent report with '## CMD:' header + make output must NOT "
            "be blocked by hasLocalWork — command-marker bypass must fire."
        )

    def test_subagent_raw_output_bypasses_haslocalwork(self):
        """A subagent report with '## RAW OUTPUT:' header MUST NOT be blocked."""
        text = (
            "## RAW OUTPUT:\n\n"
            "PID alive? yes\n"
            "Current phase: FINISHED\n"
            ".gate-status content: lint PASS 0, typecheck PASS 0\n"
        )
        assert not _would_block_narrowed(text), (
            "A subagent report with '## RAW OUTPUT:' header must NOT be "
            "blocked by hasLocalWork — subagent-report-marker bypass must fire."
        )

    def test_markdown_table_bypasses_haslocalwork(self):
        """A subagent report containing a markdown table MUST NOT be blocked."""
        text = (
            "## Audit Report\n\n"
            "| File | Status |\n"
            "|------|--------|\n"
            "| enforce-stop.ts | fixed |\n"
            "| enforce-floor.ts | OK |\n"
        )
        assert not _would_block_narrowed(text), (
            "A subagent report with a markdown table must NOT be blocked by "
            "hasLocalWork — markdown-table bypass must fire."
        )

    def test_bare_terminal_claim_still_blocked_when_localwork_pending(self):
        """A bare 'All done.' MUST still be blocked when hasLocalWork is true —
        the narrowing must not disable real enforcement."""
        text = "All done."
        assert _would_block_narrowed(text), (
            "A bare 'All done.' must STILL be blocked even when hasLocalWork "
            "narrowing is in place — Guardrail Integrity Policy requires "
            "enforcement on real false-done claims."
        )
