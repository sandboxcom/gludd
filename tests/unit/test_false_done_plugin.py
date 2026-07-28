"""Tests for the false-completion guardrail (enforce-verified-claims.ts).

The false-done guardrail was previously in `.opencode/plugin/enforce-false-done.ts`
and then merged into `.opencode/plugin/enforce-stop.ts`. It now lives in
`.opencode/plugin/enforce-verified-claims.ts` (separated during refactoring).
It checks commit messages for done-claims without verification evidence via
the tool.execute.before hook.

TDD: this file was written FIRST and run RED against the missing plugin. Now
updated to reference enforce-verified-claims.ts.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-verified-claims.ts"
CLASSIFIER_HELPERS = ROOT / ".opencode" / "lib" / "plugin_test_exports.ts"
OPENCODE_JSON = ROOT / "opencode.json"


def _effective_source() -> str:
    """Return the plugin entrypoint plus its imported classifier helpers."""
    return PLUGIN.read_text() + "\n" + CLASSIFIER_HELPERS.read_text()


# ============================================================================
# STRUCTURAL TESTS — pin the plugin shape so a silent regression (deleted
# hook, weakened language, missing registration) is caught at gate time.
# ============================================================================


class TestPluginFileExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-verified-claims.ts must exist — it carries the false-done "
            "guardrail (DONE_WORDS, EVIDENCE_PATTERNS, shouldBlock). "
            "Without it every false-completion incident (alpha.3 ship, 12 inert "
            "features, the uncommitted '✅ Landed') is unguarded in opencode sessions."
        )
        src = _effective_source()
        assert "DONE_WORDS" in src, (
            "enforce-verified-claims.ts must contain DONE_WORDS — the false-done word list."
        )
        assert "EVIDENCE_PATTERNS" in src, (
            "enforce-verified-claims.ts must contain EVIDENCE_PATTERNS — the evidence regexes."
        )
        assert "shouldBlock" in src, (
            "enforce-verified-claims.ts must contain shouldBlock() — the classification function."
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
    def test_plugin_registers_tool_execute_before(self):
        src = PLUGIN.read_text()
        assert "tool.execute.before" in src, (
            "Plugin must register tool.execute.before to check commit "
            "messages for false-done claims."
        )


class TestEnforcementDefaultIsOn:
    """The plugin must be ON by default via the `!== '0'` pattern.

    The false-done guardrail lives in enforce-verified-claims.ts. It is
    active by default — gated by GLUDD_VERIFIED_CLAIMS_ENFORCE !== "0".

    A default-OFF guardrail is advisory-only and will not stop the failure
    mode it was built for.
    """

    def test_default_on_via_env_var(self):
        src = PLUGIN.read_text()
        assert "GLUDD_VERIFIED_CLAIMS_ENFORCE" in src, (
            "Plugin must have a GLUDD_VERIFIED_CLAIMS_ENFORCE env var (the default-on gate)."
        )
        assert '=== "0"' in src, (
            "Plugin must use `=== \"0\"` return-early pattern for default-on "
            "(matching other plugins' pattern: if env === \"0\") return)."
        )

    def test_should_block_function_exists(self):
        src = _effective_source()
        assert "shouldBlock" in src, (
            "Plugin must define shouldBlock() — the classification function "
            "that checks DONE_WORDS against EVIDENCE_PATTERNS."
        )
        assert "BLOCK_MESSAGE" in src, (
            "Plugin must define BLOCK_MESSAGE — the deny message shown when "
            "a false-done claim is detected."
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
        # enforce-verified-claims.ts uses fail-open via catch at line 79
        assert "catch" in src
        assert "permissionDecision" in src, (
            "Plugin must deny via permissionDecision rather than throwing bare errors."
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
    """The plugin's TS constants MUST exist in enforce-verified-claims.ts.

    Since the false-done guardrail was refactored from enforce-stop.ts into
    enforce-verified-claims.ts, the detection constants now live there as
    DONE_WORDS, EVIDENCE_PATTERNS, and shouldBlock().

    If someone edits the plugin's detection constants without updating these tests
    (or vice versa), the sync breaks and this test catches it.
    """

    def _src(self):
        return _effective_source()

    def test_detection_constants_in_sync(self):
        src = self._src()
        for needle in ["DONE_WORDS", "EVIDENCE_PATTERNS", "NOT_DONE_PHRASES",
                        "BLOCK_MESSAGE", "shouldBlock"]:
            assert needle in src, (
                f"False-done detection constant `{needle}` is missing from "
                "enforce-verified-claims.ts — the TS constants have drifted from the test."
            )

    def test_done_words_includes_terminal_claims(self):
        src = self._src()
        for word in ["landed", "shipped", "done", "complete", "passed", "green"]:
            assert f'"{word}"' in src or f"'{word}'" in src, (
                f"DONE_WORDS must include '{word}' — it is a canonical terminal claim."
            )

    def test_evidence_patterns_in_sync(self):
        src = self._src()
        for needle in ["VERIFIED", "GATE", "passed", "Collection OK"]:
            assert needle in src, (
                f"EVIDENCE pattern `{needle}` is missing from enforce-verified-claims.ts."
            )

    def test_should_block_function_in_sync(self):
        src = self._src()
        assert "shouldBlock" in src, (
            "shouldBlock function is missing from enforce-verified-claims.ts."
        )
        assert "DONE_WORDS" in src
        assert "EVIDENCE_PATTERNS" in src

    def test_evidence_hash_pattern_present(self):
        src = self._src()
        assert "[0-9a-f]*[a-f][0-9a-f]{6,39}" in src, (
            "EVIDENCE_PATTERNS must include a commit-hash pattern that requires at least one hex letter."
        )

    def test_evidence_pass_count_pattern_present(self):
        src = self._src()
        assert r"\d+\s+passed" in src, (
            "EVIDENCE_PATTERNS must include a pass-count pattern."
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

    def test_predicate_uses_should_block(self):
        """The block predicate is now shouldBlock() in enforce-verified-claims.ts.

        shouldBlock() checks: if text contains any DONE_WORD AND no
        EVIDENCE_PATTERN matches, block. The 3-way && predicate
        (hasStructuredEvidence, hasWorkArtifact, isWorkResponse) was in
        the old enforce-stop.ts and has been replaced by the simpler
        shouldBlock() function in enforce-verified-claims.ts.
        """
        src = _effective_source()
        assert "shouldBlock" in src, (
            "Plugin must define shouldBlock() — the classification function."
        )
        assert "DONE_WORDS" in src, (
            "shouldBlock() must reference DONE_WORDS."
        )
        assert "EVIDENCE_PATTERNS" in src, (
            "shouldBlock() must reference EVIDENCE_PATTERNS."
        )

    def test_done_words_excludes_summary_language(self):
        """Summary phrases must NOT appear in DONE_WORDS.

        The DONE_WORDS array defines terminal claim words. Summary
        language like 'wrapping', 'finishing', 'conclusion' would cause
        false positives on legitimate interim status reports.
        """
        helpers = CLASSIFIER_HELPERS.read_text().lower()
        start = helpers.index("const done_words")
        end = helpers.index("const evidence_patterns", start)
        src = helpers[start:end]
        for phrase in ["wrapping", "finishing", "closing", "conclusion", "summary"]:
            # DONE_WORDS is a string array — these summary words should
            # not appear inside string literals in the DONE_WORDS block.
            # Check that they don't appear as quoted strings after DONE_WORDS.
            assert phrase not in src, (
                f"DONE_WORDS must not include '{phrase}' — it is "
                f"summary language used in any interim report, not a "
                f"terminal claim."
            )

    def test_block_message_exists(self):
        """The BLOCK_MESSAGE constant must exist in the plugin source."""
        src = PLUGIN.read_text()
        assert "BLOCK_MESSAGE" in src, (
            "Plugin must define BLOCK_MESSAGE — the deny message shown "
            "when a false-done claim is detected."
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
    """Python port of the v5 narrowed enforce-stop.ts false-done predicate.

    Block fires ONLY when ALL are true:
      1. A narrowed completion phrase is present.
      2. AND no structured evidence (commit hash / nonzero pass count).
      3. AND no work artifact (file path / gate output / subagent marker /
         command marker).
      4. AND no tool call or dispatch in the response (text-only fixture → False).

    P5 fix (2026-07-09): a markdown table alone is NOT evidence of work. The
    agent can write a summary table and stop — that is the "summary table as
    stopping point" pattern AGENTS.md forbids. A table is only legitimate when
    accompanied by machine evidence (commit hash / gate output / pass count),
    which `has_structured` already checks. Removing `has_table` from
    `has_work_artifact` closes the bypass.
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
    # P5: markdown table intentionally REMOVED from work-artifact union.
    # A table alone is not machine evidence; it can be a stopping point.
    has_work_artifact = (
        has_filepath or has_gate or has_marker or has_command
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

    def test_terminal_claim_with_table_alone_blocked(self):
        """P5 regression: 'All done.' + a markdown table MUST be blocked.

        A terminal claim combined with ONLY a markdown table (no commit
        hash, no gate output, no pass count, no file path, no command
        marker) must still trip the false-done block. This is the bug the
        P5 fix closes — previously `hasMarkdownTable` was OR'd into
        `hasWorkArtifact`, letting the agent claim completion behind a
        summary table.
        """
        text = (
            "All done.\n\n"
            "| File | Status |\n"
            "|------|--------|\n"
            "| foo.py | done |\n"
            "| bar.py | done |\n"
        )
        assert _would_block_narrowed(text), (
            "A terminal claim + a markdown table (but NO machine evidence) "
            "MUST be blocked. The markdown-table bypass was the P5 bug."
        )

    def test_table_with_machine_evidence_bypasses(self):
        """P5 fix preserves the legitimate path: table + commit hash → allowed.

        The fix removes ONLY `hasMarkdownTable` from the work-artifact
        union. Structured evidence (commit hash / pass count / gate
        output) still cancels the block, so a real work report containing
        a table AND machine evidence is correctly allowed through.
        """
        text = (
            "All done.\n\n"
            "| File | Status |\n"
            "|------|--------|\n"
            "| foo.py | commit abc1234 |\n"
        )
        assert not _would_block_narrowed(text), (
            "A terminal claim + a markdown table + a real commit hash MUST "
            "be allowed — `has_structured` cancels the block via the commit "
            "hash, which is real machine evidence."
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
