"""Behavior pin for the enforce-verified-claims guardrail.

Per AGENTS.md "Evidence-Based Response Policy" and "Done Claims Require
Observable Verification Evidence": the orchestrator repeatedly claims work
is "done", "landed", "pushed", "fixed", "passing" without pasting the
verification output (make git-log, make ci-verdict, make verify-remote,
make test-iso). This plugin blocks outgoing text containing done-words
unless it also carries machine-produced evidence.

The plugin cannot be imported into Python directly, so we read its source
as text, extract the exported DONE_WORDS array and EVIDENCE_PATTERNS regex
list, translate them to Python, re-implement the matcher, and exercise it
against the spec's test cases. Structural assertions cover hook
registration, fail-open behavior, opencode.json registration, and the
block-message contract.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-verified-claims.ts"
OPENCODE_JSON = ROOT / "opencode.json"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_done_words(src: str) -> list[str]:
    """Pull the DONE_WORDS string-literal entries out of the plugin source.

    Accepts either `export const DONE_WORDS = [...]` or a plain const. Returns
    each quoted string entry, lower-cased so the matcher is case-insensitive.
    """
    m = re.search(
        r"(?:export\s+)?const\s+DONE_WORDS[^=]*=\s*\[(.*?)\]",
        src,
        re.DOTALL,
    )
    assert m, "DONE_WORDS named export must be present in plugin source"
    return [w.lower() for w in re.findall(r'"([^"]+)"', m.group(1))]


def _extract_evidence_patterns(src: str) -> list[str]:
    """Pull the EVIDENCE_PATTERNS regex-literal bodies out of the plugin.

    The array is terminated by `] as const` (the declaration style this
    plugin uses), so we anchor on that — a naive `\\[(.*?)\\]` would stop at
    the first `]` inside a regex body like `[0-9a-f]{7,40}` and truncate.
    """
    m = re.search(
        r"(?:export\s+)?const\s+EVIDENCE_PATTERNS[^=]*=\s*\[(.*?)\]\s*as\s+const",
        src,
        re.DOTALL,
    )
    assert m, "EVIDENCE_PATTERNS named export must be present"
    # Match /pattern/flags — capture only the body, drop flags (we test
    # case-sensitively in Python by translating; the plugin uses mixed case
    # intentionally: VERIFIED, CI GREEN, etc. are uppercase tokens).
    return re.findall(r"/([^/]+)/[a-z]*", m.group(1))


def _extract_not_done_phrases(src: str) -> list[str]:
    """Pull the NOT_DONE_PHRASES scrubber regex bodies (optional export).

    The plugin scrubs in-progress phrases like "working on X" before checking
    done-words, so "working" (a done-word) used as activity rather than a
    state claim does not trigger. Returns [] if the plugin omits this
    refinement — tests that rely on the scrubber will assert its presence.
    Same `as const` anchor as _extract_evidence_patterns.
    """
    m = re.search(
        r"(?:export\s+)?const\s+NOT_DONE_PHRASES[^=]*=\s*\[(.*?)\]\s*as\s+const",
        src,
        re.DOTALL,
    )
    if not m:
        return []
    return re.findall(r"/([^/]+)/[a-z]*", m.group(1))


def _has_done_word(text: str, done_words: list[str], not_done_phrases: list[str]) -> bool:
    """Re-implementation of the plugin's hasDoneWord using extracted data."""
    lower = text.lower()
    # Scrub in-progress phrases first — "working on X" is activity, not a
    # completion claim, so "working" should not count as a done word there.
    for phrase in not_done_phrases:
        lower = re.sub(phrase, " ", lower, flags=re.IGNORECASE)
    return any(re.search(rf"\b{re.escape(w)}\b", lower) for w in done_words)


def _has_evidence(text: str, patterns: list[str]) -> bool:
    """Re-implementation of the plugin's hasEvidence."""
    return any(re.search(p, text) for p in patterns)


def _verdict(text: str) -> str:
    """Return 'block' or 'allow' per the plugin's shouldBlock logic."""
    src = _plugin_source()
    done_words = _extract_done_words(src)
    evidence = _extract_evidence_patterns(src)
    not_done = _extract_not_done_phrases(src)
    if not _has_done_word(text, done_words, not_done):
        return "allow"
    if _has_evidence(text, evidence):
        return "allow"
    return "block"


# --------------------------------------------------------------------------- #
# Structural: plugin file, registration, hook surface, named exports.
# --------------------------------------------------------------------------- #
class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            f"enforce-verified-claims.ts must exist at {PLUGIN_PATH}"
        )

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads(OPENCODE_JSON.read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-verified-claims" in p for p in plugins), (
            "enforce-verified-claims.ts must be registered in opencode.json "
            "plugin[] array"
        )

    def test_text_complete_hook_registered(self):
        src = _plugin_source()
        # opencode exposes the surface as `experimental.text.complete`
        # (see enforce-stop.ts line 642 for the established pattern).
        assert "text.complete" in src, (
            "plugin must register a text.complete hook surface"
        )

    def test_exports_done_words(self):
        src = _plugin_source()
        assert "DONE_WORDS" in src, "DONE_WORDS named export missing"

    def test_exports_evidence_patterns(self):
        src = _plugin_source()
        assert "EVIDENCE_PATTERNS" in src, "EVIDENCE_PATTERNS named export missing"

    def test_exports_should_block_function(self):
        src = _plugin_source()
        assert re.search(
            r"export\s+(async\s+)?function\s+shouldBlock|"
            r"export\s+const\s+shouldBlock",
            src,
        ), "plugin must export a shouldBlock function for testability"

    def test_fail_open_present(self):
        src = _plugin_source()
        assert "catch" in src, (
            "plugin must wrap the hook body in try/catch for fail-open behavior"
        )

    def test_env_var_disable_present(self):
        src = _plugin_source()
        assert "GLUDD_VERIFIED_CLAIMS_ENFORCE" in src, (
            "plugin must honor GLUDD_VERIFIED_CLAIMS_ENFORCE=0 to disable"
        )

    def test_block_message_contract(self):
        src = _plugin_source()
        # The block message must name the remediation commands so the agent
        # knows what to run to produce real evidence.
        assert "git-log" in src or "git-status" in src, (
            "block message must point at make git-log / make git-status"
        )
        assert "ci-verdict" in src, (
            "block message must point at make ci-verdict-safe"
        )


# --------------------------------------------------------------------------- #
# Required done-words — the plugin must ship at least this set so the test
# fixtures below resolve. Asserting presence guards against accidental
# list shrinkage that would silently weaken the guardrail.
# --------------------------------------------------------------------------- #
class TestDoneWordsContract:
    def test_required_done_words_present(self):
        words = _extract_done_words(_plugin_source())
        required = [
            "landed", "committed", "pushed", "fixed", "passing",
            "shipped", "done", "complete", "green", "resolved",
        ]
        for w in required:
            assert w in words, (
                f"DONE_WORDS must contain {w!r} (per AGENTS.md Evidence-Based "
                f"Response Policy). Current: {words!r}"
            )


# --------------------------------------------------------------------------- #
# Behavioral verdicts — the 7+ spec test cases.
# --------------------------------------------------------------------------- #
class TestMatcherVerdicts:
    """The plugin must block done-words without evidence and allow with it."""

    def test_done_word_without_evidence_blocked(self):
        # "commit landed" with no hash, no VERIFIED, no pass count → BLOCK.
        assert _verdict("commit landed") == "block"

    def test_done_word_with_commit_hash_allowed(self):
        # A 7-char hex hash is the canonical commit-hash evidence token.
        assert _verdict("commit landed `abc1234`") == "allow"

    def test_done_word_with_verified_allowed(self):
        # VERIFIED <branch>@<sha> is make verify-remote output.
        assert _verdict("pushed, VERIFIED master@abc1234") == "allow"

    def test_done_word_with_test_count_allowed(self):
        # "N passed" is make test-iso / pytest output.
        assert _verdict("tests passing, 10 passed") == "allow"

    def test_no_done_word_not_blocked(self):
        # "working on the fix" — "working" followed by "on" is in-progress
        # activity, not a state claim. The scrubber must exclude it.
        assert _verdict("working on the fix") == "allow"

    def test_evidence_without_done_word_not_blocked(self):
        # A bare commit hash with no done-word is not a claim — allow.
        assert _verdict("abc1234") == "allow"

    def test_multiple_done_words_need_only_one_evidence(self):
        # Three done-words, one evidence token (the full verify-remote shape
        # VERIFIED <branch>@<sha>) → allowed.
        assert _verdict("landed and pushed and fixed, VERIFIED master@abc1234") == "allow"

    def test_done_word_with_ci_green_allowed(self):
        # "CI GREEN" is make ci-verdict output.
        assert _verdict("CI GREEN — the change landed") == "allow"

    def test_done_word_with_gate_passed_allowed(self):
        # "=== GATE: PASSED ===" is the gate terminal marker.
        assert _verdict("=== GATE: PASSED ===\nfeature shipped") == "allow"

    def test_passing_alone_blocked(self):
        # "tests are passing" with no count/hash/VERIFIED → blocked.
        assert _verdict("tests are passing") == "block"

    def test_green_alone_blocked(self):
        # "the gate is green" with no gate marker → blocked.
        assert _verdict("the gate is green") == "block"

    def test_working_as_state_claim_blocked(self):
        # "it's working now" — "working" NOT followed by "on" is a state
        # claim (the thing works), so it SHOULD be blocked without evidence.
        assert _verdict("it's working now") == "block"


# --------------------------------------------------------------------------- #
# Fail-open contract — any throw inside the hook must not wedge the editor.
# --------------------------------------------------------------------------- #
class TestFailOpenContract:
    def test_catch_does_not_rethrow_non_deny(self):
        src = _plugin_source()
        catch_idx = src.find("catch")
        assert catch_idx != -1, "must have a catch block for fail-open"
        window = src[catch_idx:catch_idx + 400]
        # The catch re-throws only for permissionDecision==="deny" errors;
        # all other throwables (malformed input, type errors) are swallowed.
        assert "permissionDecision" in window, (
            "catch must check permissionDecision before re-throwing"
        )


# --------------------------------------------------------------------------- #
# tool.execute.before hook — commit-message enforcement.
# --------------------------------------------------------------------------- #
class TestToolExecuteBefore:
    """The tool.execute.before hook blocks commit-shaped make targets whose
    MSG= parameter contains done-claims without evidence. It is the surviving
    enforcement surface after text.complete was removed.
    """

    def test_tool_execute_before_hook_registered(self):
        """Source must define a tool.execute.before hook (single string key)."""
        src = _plugin_source()
        assert src.count('"tool.execute.before"') >= 1, (
            "tool.execute.before hook must be registered in defaultImpl"
        )

    def test_tool_execute_before_subagent_guard(self):
        """must call isSubagent() and return early in subagent context."""
        src = _plugin_source()
        # The defaultImpl block and the export default block both have
        # isSubagent() guards.
        assert src.count("isSubagent()") >= 2, (
            "tool.execute.before must guard with isSubagent() in both "
            "defaultImpl and export default"
        )

    def test_tool_execute_before_env_var_disable(self):
        """must honor GLUDD_VERIFIED_CLAIMS_ENFORCE=0."""
        src = _plugin_source()
        assert "GLUDD_VERIFIED_CLAIMS_ENFORCE" in src, (
            "tool.execute.before must honor GLUDD_VERIFIED_CLAIMS_ENFORCE=0"
        )

    def test_tool_execute_before_bash_only(self):
        """must only fire for bash/Bash tool — falls through for others."""
        src = _plugin_source()
        # The guard uses `tool !== "bash" && tool !== "Bash"` (current code);
        # older builds used a toolName variable. Accept either.
        bash_only = (
            ('toolName !== "bash"' in src and 'toolName !== "Bash"' in src)
            or ('tool !== "bash"' in src and 'tool !== "Bash"' in src)
            or 'tool !== "bash" && tool !== "Bash"' in src
        )
        assert bash_only, "tool.execute.before must return early for non-bash tools"

    def test_tool_execute_before_commit_targets(self):
        """must only fire for commit-shaped make targets."""
        src = _plugin_source()
        assert "git-commit" in src, (
            "tool.execute.before must check for git-commit in the command"
        )
        required = ["git-commit", "commit-no-verify", "repo-commit",
                    "ship-commit", "test-and-commit"]
        for target in required:
            assert target in src, (
                f"tool.execute.before must include {target} in commit-target set"
            )

    def test_tool_execute_before_msg_extraction(self):
        """must extract MSG= parameter from the bash command args."""
        src = _plugin_source()
        assert "MSG" in src, (
            "tool.execute.before must extract MSG from the command"
        )
        # The current code reads args.command (via ctx.args); older builds used
        # a toolInput variable. Accept either shape.
        assert "args" in src or "toolInput" in src, (
            "tool.execute.before must read the command args to find MSG"
        )

    def test_tool_execute_before_calls_should_block(self):
        """must call shouldBlock() on the extracted message."""
        src = _plugin_source()
        assert "shouldBlock" in src, (
            "tool.execute.before must delegate to shouldBlock for verdict"
        )

    def test_tool_execute_before_deny_throw(self):
        """must throw with permissionDecision: deny on violation."""
        src = _plugin_source()
        assert 'permissionDecision' in src and '"deny"' in src, (
            "tool.execute.before must throw permissionDecision: deny on block"
        )

    def test_tool_execute_before_non_commit_passthrough(self):
        """must return (allow) for non-commit make targets like make test."""
        src = _plugin_source()
        # The regex only matches commit targets; everything else falls through.
        assert "cmd.startsWith" in src, (
            "tool.execute.before must check cmd.startsWith('make ') before matching"
        )
