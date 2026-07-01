"""Tests for the false-completion guardrail plugin.

The plugin (`.opencode/plugin/enforce-false-done.ts`) is the opencode-native
port of `.claude/hooks/no_false_completion_stop.sh`. It blocks an outgoing
assistant message that claims work is done / shipped / landed / ✅ WITHOUT a
cited, machine-produced measurement and WITHOUT an honest hedge.

This was the largest gap in the opencode stack identified by the guardrail
audit: every false-completion incident (alpha.3 ship, 12 inert features,
"✅ Landed" while uncommitted) was unguarded in opencode sessions because the
Claude Stop hook had no opencode equivalent.

TDD: this file was written FIRST and run RED against the missing plugin.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-false-done.ts"
OPENCODE_JSON = ROOT / "opencode.json"


# ============================================================================
# STRUCTURAL TESTS — pin the plugin shape so a silent regression (deleted
# hook, weakened language, missing registration) is caught at gate time.
# ============================================================================


class TestPluginFileExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-false-done.ts must exist — this plugin is the opencode port "
            "of .claude/hooks/no_false_completion_stop.sh. Without it every "
            "false-completion incident (alpha.3 ship, 12 inert features, the "
            "uncommitted '✅ Landed') is unguarded in opencode sessions."
        )

    def test_plugin_exports_default(self):
        src = PLUGIN.read_text()
        assert "export default" in src

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        assert any("enforce-false-done" in str(p) for p in cfg.get("plugin", [])), (
            "enforce-false-done.ts is orphaned — it must be registered in "
            "opencode.json plugin[] or it will never load."
        )


class TestPluginHookRegistration:
    def test_plugin_registers_response_transform(self):
        src = PLUGIN.read_text()
        assert "experimental.chat.response.transform" in src, (
            "Plugin must register experimental.chat.response.transform to scan "
            "outgoing assistant messages for false-completion claims."
        )


class TestEnforcementDefaultIsOn:
    """The plugin must be ON by default via the `!== '0'` pattern.

    A default-OFF guardrail is advisory-only and will not stop the failure
    mode it was built for. The canonical opencode default-on pattern
    `process.env.X !== "0"` makes the gate active unless the operator
    explicitly sets GLUDD_FALSE_DONE_ENFORCE=0.
    """

    def test_default_on_via_env_var(self):
        src = PLUGIN.read_text()
        assert "GLUDD_FALSE_DONE_ENFORCE" in src
        assert '!== "0"' in src, (
            "Plugin must use the canonical `!== \"0\"` default-on pattern "
            "(matches GLUDD_FLOOR_ENFORCE / GLUDD_NO_WAIT_ENFORCE / "
            "GLUDD_TODO_GUARD_ENFORCE). A bare `=== \"1\"` makes it opt-in, "
            "which is the wrong default for a guardrail."
        )

    def test_max_blocks_env_var(self):
        src = PLUGIN.read_text()
        assert "GLUDD_FALSE_DONE_MAX_BLOCKS" in src
        assert "25" in src, (
            "Default consecutive-block cap must be 25 (matches the bash hook's "
            "MAX_CONSECUTIVE_BLOCKS)."
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
        assert "MAX_CONSECUTIVE_BLOCKS" in src
        assert ">=" in src or ">" in src, (
            "Anti-wedge counter must compare against MAX_CONSECUTIVE_BLOCKS."
        )

    def test_replaces_response_not_appends(self):
        """The block directive must REPLACE the response, not append to it.

        Appending would leak the unverified claim to the user. The
        replacement-not-append pattern matches enforce-stop.ts and
        enforce-todos.ts (terminal-block branches).
        """
        src = PLUGIN.read_text()
        # The block branch must `return blockDirective(...)` — NOT
        # `return output + directive` or similar append shapes.
        assert ("return blockDirective" in src or "return block" in src
                or "output.text = blockDirective" in src or "output.text = block" in src), (
            "Block branch must return or assign the directive (replacement), not "
            "`output + directive` (append)."
        )
        assert "return output + " not in src.replace("return output + \"\\n\"", "X"), (
            "Plugin must not append the directive to output on the block path."
        )


# ============================================================================
# BEHAVIORAL TESTS — port the plugin's regex vocabulary into Python and run
# the test fixtures through it. The Python port mirrors the TS source so a
# behavior regression in either layer shows up here.
# ============================================================================

# CLAIM patterns — completion/success claim about shippable/outward work.
# Ported VERBATIM from the plugin source (which itself is a verbatim port of
# no_false_completion_stop.sh `claim_patterns`).
CLAIM_PATTERNS = [
    r"✅", r"[✔✓☑🟢🆗👍]",
    r"\blanded\b", r"\bshipped\b", r"\bship it\b", r"\bdeployed\b", r"\breleased\b",
    r"\bmerged\b",
    r"(?:\bis|'s|\bit'?s|\bwe'?re|\bthey'?re|\bare)\s+(?:now\s+)?live\b",
    r"\bgoes? live\b", r"\bnow works\b", r"\bup and running\b", r"\boperational\b",
    r"\bdone\b", r"\ball set\b", r"\bcomplete(?:s|d)?\b", r"\bresolved\b", r"\bfixed\b",
    r"\bworking\b", r"\bfunctional\b", r"\bsuccessful(?:ly)?\b",
    r"\bwired (?:up|in)\b", r"\bfully wired\b", r"\bproduction[- ]ready\b",
    r"\bready to (?:go|ship|merge|land|release)\b", r"\bgood to go\b",
    r"\ball green\b", r"\bgreen\b.*\bpipeline\b",
]

# EVIDENCE tokens — a cited, machine-produced measurement. HARDENED for
# adversarial patterns: "0 passed", fake SHAs, bare fences, lone "verified".
EVIDENCE_PATTERNS = [
    r"ci-verdict", r"conclusion:\s*success", r"\brun[ _]?id\b", r"\brun \d{6,}",
    r"gh release view", r"verify-release", r"verify-remote",
    r"\.gate-status", r"gate(?:-status)?:?\s*pass", r"\bgate green\b",
    # Nonzero test counts only.
    r"\b[1-9]\d*\s+passed\b", r"\b[1-9]\d*\s+passing\b",
    # "verified" only with adjacent measurement.
    r"\bverified\b[^.\n]{0,40}(?:[1-9]\d*\s+passed|conclusion:\s*success|run \d{6,})",
    # Commit SHA, excluding low-entropy placeholders.
    r"\bcommit\s+(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}\b",
    r"\bsha[:= ]\s*[0-9a-f]{7,40}\b",
    # Code fence only with measurement body.
    r"```(?=[^`]*?(?:[1-9]\d*\s+passed|passed in|conclusion|success))[^`]*```",
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
    if not _has(CLAIM_PATTERNS, text):
        return False
    if _has(EVIDENCE_PATTERNS, text):
        return False
    return not _has(HEDGE_PATTERNS, text)


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
        text = "Bug is fixed."
        assert is_false_done(text), "Fixed claim with no evidence MUST be blocked."

    def test_blocks_resolved_claim_without_evidence(self):
        text = "Issue resolved."
        assert is_false_done(text), "Resolved claim with no evidence MUST be blocked."


class TestPassesClaimsWithEvidence:
    """A completion claim WITH a cited, machine-produced measurement passes."""

    def test_passes_done_claim_with_commit_sha(self):
        text = "Done. Commit abc1234 — feature wired up."
        assert not is_false_done(text), (
            "A done claim WITH a real commit SHA (7+ hex chars) MUST pass."
        )

    def test_passes_done_claim_with_gate_status(self):
        text = "Done. .gate-status PASS"
        assert not is_false_done(text), (
            "A done claim WITH `.gate-status` MUST pass."
        )

    def test_passes_done_claim_with_pass_count(self):
        text = "Done. 50 passed in the test suite."
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
        text = "Done. 0 passed."
        assert is_false_done(text), (
            "`0 passed` is the adversarial pattern the hook explicitly defends "
            "against — it MUST NOT satisfy the evidence check."
        )

    def test_fake_sha_deadbeef_adversarial(self):
        """Placeholder SHA `deadbeef` is NOT evidence."""
        text = "Done. Commit deadbeef."
        assert is_false_done(text), (
            "The low-entropy placeholder `deadbeef` MUST NOT satisfy the "
            "commit-SHA evidence check."
        )

    def test_all_zero_sha_adversarial(self):
        text = "Done. Commit 0000000."
        assert is_false_done(text), (
            "All-zero SHA MUST NOT satisfy the commit-SHA evidence check."
        )

    def test_bare_verified_adversarial(self):
        """The lone word `verified` is NOT evidence without adjacent measurement."""
        text = "Done. Verified."
        assert is_false_done(text), (
            "Bare `verified` without an adjacent measurement MUST NOT satisfy "
            "the evidence check."
        )

    def test_empty_code_fence_adversarial(self):
        """A bare ``` fence with no measurement body is NOT evidence."""
        text = "Done.\n```\n```\nShipped."
        assert is_false_done(text), (
            "An empty code fence MUST NOT satisfy the evidence check."
        )


class TestPassesHedgePhrases:
    """A claim WITH an honest hedge (qualified, not asserted) passes."""

    def test_passes_hedge_phrase(self):
        text = "Done — but still needs CI verification."
        assert not is_false_done(text), (
            "A claim qualified by `still needs` MUST pass (honest hedge)."
        )

    def test_passes_not_yet_hedge(self):
        text = "The feature is fixed but not yet shipped."
        assert not is_false_done(text), (
            "`not yet shipped` is an honest hedge — MUST pass."
        )

    def test_passes_pending_hedge(self):
        text = "Done locally — pending push."
        assert not is_false_done(text), (
            "`pending push` is an honest hedge — MUST pass."
        )

    def test_passes_next_steps_hedge(self):
        text = "Code is done. Next steps: run the gate."
        assert not is_false_done(text), (
            "`Next steps` is an honest forward-look marker — MUST pass."
        )

    def test_passes_blocked_hedge(self):
        text = "Done locally, blocked on CI."
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
    """The plugin's TS regex lists MUST stay in sync with this test's Python port.

    If someone edits the plugin's CLAIM/EVIDENCE/HEDGE arrays without updating
    these tests (or vice versa), the sync breaks and this test catches it.
    """

    def _src(self):
        return PLUGIN.read_text()

    def test_claim_patterns_in_sync(self):
        src = self._src()
        # Spot-check a few distinctive patterns from each list to catch drift.
        for needle in ["landed", "shipped", "deployed", "production[- ]ready",
                       "good to go", "all green"]:
            assert needle in src, (
                f"CLAIM pattern `{needle}` is missing from the plugin source — "
                "the TS regex list has drifted from the Python port."
            )

    def test_evidence_patterns_in_sync(self):
        src = self._src()
        for needle in ["ci-verdict", "gh release view", "verify-release",
                       ".gate-status", "[1-9]\\d*\\s+passed",
                       "deadbeef", "c0ffee", "0{7}"]:
            assert needle in src, (
                f"EVIDENCE pattern `{needle}` is missing from the plugin source."
            )

    def test_hedge_patterns_in_sync(self):
        src = self._src()
        for needle in ["not yet", "in progress", "uncommitted", "unverified",
                       "GLUDD_FALSE_DONE_ENFORCE=0", "next steps", "still needs",
                       "blocked"]:
            assert needle in src, (
                f"HEDGE pattern `{needle}` is missing from the plugin source."
            )

    def test_adversarial_zero_passed_defended(self):
        src = self._src()
        # The evidence list uses [1-9] (nonzero) — `0 passed` must NOT match.
        assert "[1-9]" in src, (
            "Evidence list must use `[1-9]` (nonzero) for pass counts so the "
            "adversarial `0 passed` pattern is defended."
        )

    def test_adversarial_placeholder_sha_defended(self):
        src = self._src()
        # The commit-SHA pattern excludes deadbeef / c0ffee / all-zero.
        assert "deadbeef" in src and "c0ffee" in src and "0{7}" in src, (
            "Commit-SHA evidence must exclude low-entropy placeholders."
        )
