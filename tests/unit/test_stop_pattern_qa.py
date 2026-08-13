"""Q&A stop-pattern detection in enforce-stop.ts.

The Q&A-response-summary stop pattern was the bug behind BUGS.md #7 and #10:
the agent sent text-only "what was done" recaps ("completed in this session",
"everything committed and merged", "**What changed?**", "**What's left?**")
while work remained — a premature stop in Q&A disguise.

The enforce-stop.ts plugin now defines `QA_RESPONSE_PATTERNS` and consults it
in the `experimental.text.complete` hook. When a response matches AND pending
work exists AND no tool calls were made, the text is blanked with a
"QA RESPONSE SUMMARY BLOCKED" directive.

This test pins the structural presence of the regex constant and its hook
integration. Behavioral tests (asserting the actual blanking logic) are in
`test_false_done_plugin.py` (which tests `_would_block_narrowed`).

AGENTS.md "CRITICAL: Q&A Response Pattern — Answer THEN Continue" section
documents the policy; this test is the enforcement pin (layer 3 of the
guardrail pattern).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
_IMPL = ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"


def _src() -> str:
    wrapper = PLUGIN.read_text()
    if _IMPL.exists():
        return _IMPL.read_text() + "\n" + wrapper
    return wrapper


class TestQaResponsePatternsDefined:
    """The plugin must define QA_RESPONSE_PATTERNS with the BUGS.md #7/#10 phrases."""

    def test_constant_exists(self):
        assert "QA_RESPONSE_PATTERNS" in _src(), (
            "enforce-stop.ts must define QA_RESPONSE_PATTERNS — BUGS.md #7 and "
            "#10 were caused by missing detection for Q&A-style 'what was done' "
            "recaps that answer a question but stop without a tool call."
        )

    def _extract_regex_body(self) -> str:
        src = _src()
        m = re.search(r"QA_RESPONSE_PATTERNS\s*=\s*/([^/\n]+)/", src)
        assert m, "QA_RESPONSE_PATTERNS regex literal not found"
        return m.group(1).lower()

    def test_includes_completed_in_this_session(self):
        body = self._extract_regex_body()
        assert "completed in this session" in body, (
            "QA_RESPONSE_PATTERNS must match 'completed in this session' — a "
            "classic Q&A answer phrase that summarizes work without continuing it."
        )

    def test_includes_done_since_crash(self):
        body = self._extract_regex_body()
        assert "done since the" in body, (
            "QA_RESPONSE_PATTERNS must match 'was done since the crash' / "
            "'was done since the last session' — a recurring Q&A recap pattern."
        )

    def test_includes_everything_committed(self):
        body = self._extract_regex_body()
        assert "everything" in body and "committed" in body, (
            "QA_RESPONSE_PATTERNS must match 'everything committed' / 'everything "
            "committed and merged' — explicitly named in the task spec."
        )

    def test_includes_heres_what_was_done(self):
        body = self._extract_regex_body()
        assert "here" in body and "what was" in body, (
            "QA_RESPONSE_PATTERNS must match 'Here's what was done/completed' — "
            "the canonical Q&A recap lead-in phrase."
        )

    def test_includes_summary_of_what_was_done(self):
        body = self._extract_regex_body()
        assert "summary of what was" in body, (
            "QA_RESPONSE_PATTERNS must match 'Summary of what was done/completed' — "
            "an explicit summary framing."
        )

    def test_includes_bolded_question_headers(self):
        body = self._extract_regex_body()
        assert "what" in body and "changed" in body, (
            "QA_RESPONSE_PATTERNS must match bolded question-style recap headers: "
            "'**What changed?**', '**What was done?**', '**What is left?**' — the "
            "structural shape documented in BUGS.md #10."
        )

    def test_regex_is_case_insensitive(self):
        src = _src()
        m = re.search(r"QA_RESPONSE_PATTERNS\s*=\s*/[^/\n]+/([a-z]+)", src)
        assert m, "QA_RESPONSE_PATTERNS regex flags not found"
        flags = m.group(1)
        assert "i" in flags, (
            "QA_RESPONSE_PATTERNS must be case-insensitive (the `/i` flag) "
            "so 'Completed in this session' and 'COMPLETED IN THIS SESSION' both match."
        )


class TestQaResponsePatternsWired:
    """The constant must be consulted in the text.complete hook."""

    def test_referenced_in_text_complete(self):
        src = _src()
        assert re.search(r"QA_RESPONSE_PATTERNS\.test\s*\(", src), (
            "QA_RESPONSE_PATTERNS must be consulted via .test() somewhere in "
            "the plugin — defining the constant without consulting it is "
            "dead code (Guardrail Integrity Policy)."
        )

    def test_constant_not_in_dead_array(self):
        """Guard against regressing to a giant vocabulary list.

        The plugin was deliberately made lean (NO_WAIT_PATTERNS and
        CONSTRAINT_AS_STOP_PATTERNS removed). QA_RESPONSE_PATTERNS must
        remain a SINGLE regex literal — not an array of strings — so it
        cannot silently grow back into the unbounded vocabulary pattern.
        """
        src = _src()
        assert not re.search(r"QA_RESPONSE_PATTERNS\s*=\s*\[", src), (
            "QA_RESPONSE_PATTERNS must be a regex literal (not an array). "
            "An array would regress toward the unbounded vocabulary lists "
            "the lean-plugin refactor removed."
        )


class TestQaBlockDirective:
    """The HARD STOP directive output must be recognizable."""

    def test_block_message_contains_qa_response_summary_blocked(self):
        src = _src()
        assert "QA RESPONSE SUMMARY BLOCKED" in src, (
            "The QA summary block directive must contain the exact string "
            "'QA RESPONSE SUMMARY BLOCKED' so the blocked response is "
            "unambiguously identifiable in the session transcript."
        )

    def test_block_message_instructs_dispatch(self):
        src = _src()
        assert re.search(r"(?:DISPATCH|dispatch).*(?:TOOL CALL|A TOOL CALL|SUBAGENTS)", src), (
            "The QA summary block message must instruct the agent to dispatch "
            "a tool call — otherwise the block is a dead end that produces a "
            "\"help, I'm blocked\" response instead of guiding the agent to "
            "correct behavior."
        )

    def test_block_reason_qa_response_summary_stop(self):
        src = _src()
        assert re.search(r"recordBlock\s*\(\s*['\"]qa-response-summary-stop\b", src), (
            "The block must call recordBlock('qa-response-summary-stop') so "
            "the block-reason audit file is machine-readable."
        )

    def test_log_false_done_block_qa_summary(self):
        src = _src()
        assert re.search(r"logFalseDoneBlock\s*\([^)]*['\"]qa-response-summary-stop\b", src), (
            "The block must call logFalseDoneBlock(..., 'qa-response-summary-stop') so "
            "the false-done block audit file is machine-readable."
        )


class TestQaVsSubagentReportSeparation:
    """Q&A response patterns are structurally different from subagent reports.

    A subagent final report carries work-product markers ("Files changed:",
    "Test results", "## Report"). These are legitimate and must NOT be blocked.
    A Q&A summary is conversational, directed at the user, and carries phrases
    like "completed in this session", "everything committed and merged",
    "**What changed?**" — it is the main agent summarizing for the user, not
    relaying work from a subagent.

    This separation is structural: QA_RESPONSE_PATTERNS does not overlap with
    SUBAGENT_REPORT_MARKERS.
    """

    def test_qa_patterns_do_not_overlap_subagent_markers(self):
        src = _src()
        qa_body_m = re.search(r"QA_RESPONSE_PATTERNS\s*=\s*/([^/\n]+)/", src)
        assert qa_body_m, "QA_RESPONSE_PATTERNS regex not found for overlap check"
        qa_body = qa_body_m.group(1).lower()
        subagent_tokens = [
            "files changed", "files edited",
            "test result", "test results",
            "report", "raw output",
            "exit code",
        ]
        for token in subagent_tokens:
            assert token not in qa_body, (
                f"QA_RESPONSE_PATTERNS must not contain '{token}' — this is a "
                "subagent-report marker, not a Q&A-stop phrase. Conflating the "
                "two would cause subagent results to be incorrectly blocked as "
                "Q&A summaries."
            )
