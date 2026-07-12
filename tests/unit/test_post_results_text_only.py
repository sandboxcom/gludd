"""Post-results text-only detection in enforce-stop.ts.

Implements the fix for the "premature stop after subagent results" failure mode:
after subagent results arrive, the agent can send a text-only response (summary/
status) with no tool call, and the plugin doesn't block it.

The enforce-stop.ts plugin now:
1. Tracks last-turn subagent result presence via POST_RESULTS_STATE_FILE
2. Blocks text-only responses after subagent results (after-results-text-only)
3. Enforces a consecutive-text-only limit (max 1 when work pending)
4. Integrates with enforce-floor.ts's shared streak counter on block

TDD: this file was written FIRST to assert the block behavior before the
plugin was patched.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"


def _src() -> str:
    return PLUGIN.read_text()


class TestPostResultsStateConstants:
    """The plugin must define state file constants for post-results tracking."""

    def test_post_results_state_file_defined(self):
        src = _src()
        assert "POST_RESULTS_STATE_FILE" in src, (
            "enforce-stop.ts must define POST_RESULTS_STATE_FILE — the state file "
            "that tracks whether the previous turn had subagent result markers. "
            "Without this, the after-results-text-only block has no memory across turns."
        )

    def test_text_only_state_file_defined(self):
        src = _src()
        assert "TEXT_ONLY_STATE_FILE" in src, (
            "enforce-stop.ts must define TEXT_ONLY_STATE_FILE — the state file "
            "that tracks consecutive text-only responses. Without this, the "
            "consecutive-text-only limit has no memory across turns."
        )

    def test_state_file_paths_in_tmp(self):
        src = _src()
        assert "/tmp/gludd-post-results-state.json" in src, (
            "POST_RESULTS_STATE_FILE must write to /tmp/gludd-post-results-state.json"
        )
        assert "/tmp/gludd-text-only-state.json" in src, (
            "TEXT_ONLY_STATE_FILE must write to /tmp/gludd-text-only-state.json"
        )


class TestPostResultsStateFunctions:
    """The plugin must define read/write functions for post-results state."""

    def test_read_post_results_state_exists(self):
        src = _src()
        assert re.search(r"function\s+readPostResultsState\s*\(", src), (
            "readPostResultsState function must exist — reads the post-results "
            "state file for cross-turn memory."
        )

    def test_write_post_results_state_exists(self):
        src = _src()
        assert re.search(r"function\s+writePostResultsState\s*\(", src), (
            "writePostResultsState function must exist — persists the post-results "
            "state for the next turn."
        )

    def test_read_text_only_state_exists(self):
        src = _src()
        assert re.search(r"function\s+readTextOnlyState\s*\(", src), (
            "readTextOnlyState function must exist — reads the text-only counter."
        )

    def test_write_text_only_state_exists(self):
        src = _src()
        assert re.search(r"function\s+writeTextOnlyState\s*\(", src), (
            "writeTextOnlyState function must exist — persists the text-only counter."
        )

    def test_text_has_result_markers_exists(self):
        src = _src()
        assert re.search(r"function\s+textHasResultMarkers\s*\(", src), (
            "textHasResultMarkers function must exist — detects subagent result "
            "markers in the agent's response text."
        )


class TestTextHasResultMarkers:
    """The result-marker detector must cover canonical result phrases."""

    def test_includes_task_result(self):
        src = _src()
        m = re.search(r"textHasResultMarkers[\s\S]{0,300}?\[[\s\S]{0,200}?\]", src, re.DOTALL)
        assert m, "textHasResultMarkers and its marker array not found"
        body = m.group(0).lower()
        assert "task result" in body or (
            '"task result"' in body
        ), "Result marker array must include 'task result'."

    def test_includes_subagent_result(self):
        body = _src().lower()
        assert "subagent result" in body or "subagent_result" in body, (
            "Result marker array must include 'subagent result'."
        )

    def test_includes_workflow_result(self):
        body = _src().lower()
        assert "workflow result" in body or "workflow_result" in body, (
            "Result marker array must include 'workflow result'."
        )


class TestAfterResultsTextOnlyBlock:
    """The text.complete hook must block text-only responses after subagent results."""

    def test_check_toolcall_made_and_dispatch_count(self):
        """The text-only detection must check turnState.toolCallMade and dispatchCount."""
        src = _src()
        assert re.search(
            r"!turnState\.toolCallMade\s*&&\s*turnState\.dispatchCount\s*===?\s*0",
            src,
        ), (
            "The text-only guard condition must check both turnState.toolCallMade "
            "(was any tool called?) and dispatchCount === 0 (no dispatches). "
            "A response with tool calls is not text-only."
        )

    def test_block_message_contains_resume_work(self):
        src = _src()
        assert "RESUME WORK: dispatch subagents immediately" in src, (
            "The after-results-text-only block message MUST contain 'RESUME WORK: "
            "dispatch subagents immediately' so the agent knows exactly what to do."
        )

    def test_block_uses_log_false_done_block(self):
        src = _src()
        assert re.search(
            r"logFalseDoneBlock\s*\([^)]*['\"]after-results-text-only\b",
            src,
        ), (
            "The block must call logFalseDoneBlock(..., 'after-results-text-only') "
            "so the false-done block audit file is machine-readable."
        )

    def test_block_uses_record_block(self):
        src = _src()
        assert re.search(
            r"recordBlock\s*\(\s*['\"]after-results-text-only\b",
            src,
        ), (
            "The block must call recordBlock('after-results-text-only') so "
            "the block-reason audit file is machine-readable."
        )

    def test_block_increments_shared_streak(self):
        src = _src()
        # The text-only block path must call updateSharedStreak to integrate
        # with enforce-floor.ts's streak counter
        assert "updateSharedStreak" in src, (
            "updateSharedStreak must be called from the text-only block path "
            "to integrate with enforce-floor.ts's streak counter."
        )

    def test_post_results_state_read_before_check(self):
        src = _src()
        assert re.search(
            r"readPostResultsState\s*\(\s*\)[\s\S]{0,500}?lastTurnHadResults",
            src,
        ), (
            "readPostResultsState() must be called BEFORE checking "
            "lastTurnHadResults — the state file must be read to know "
            "whether the previous turn had subagent results."
        )


class TestConsecutiveTextOnlyLimit:
    """The text.complete hook must enforce a consecutive-text-only limit."""

    def test_consecutive_text_only_block_exists(self):
        src = _src()
        assert "CONSECUTIVE TEXT-ONLY RESPONSES" in src or "consecutive-text-only" in src, (
            "The consecutive text-only block must exist — blocks the 2nd+ "
            "text-only response when work is pending."
        )

    def test_text_only_count_check_at_least_2(self):
        src = _src()
        assert re.search(r"textOnly\.count\s*>=?\s*2", src), (
            "The consecutive text-only limit must trigger at count >= 2 — "
            "at most 1 text-only response per session when work is pending."
        )

    def test_text_only_count_reset_on_tool_call(self):
        src = _src()
        assert re.search(
            r"writeTextOnlyState\s*\(\s*\{.*?count.*?:.*?0.*?\}",
            src,
        ), (
            "The text-only counter must be reset (count: 0) when the agent "
            "makes tool calls — section marked 'Reset text-only counter'."
        )

    def test_block_uses_consecutive_text_only_reason(self):
        src = _src()
        assert re.search(
            r"recordBlock\s*\(\s*['\"]consecutive-text-only\b",
            src,
        ), (
            "The block must call recordBlock('consecutive-text-only')."
        )

    def test_same_session_detection_uses_300s_window(self):
        src = _src()
        assert "300_000" in src, (
            "Text-only session detection must use a 300s (5min) window — "
            "sameSession = (now - lastTs) < 300_000."
        )


class TestResultMarkerDetectionAtTurnEnd:
    """Result markers must be detected at end of text.complete for next turn."""

    def test_update_post_results_at_end_of_text_complete(self):
        src = _src()
        assert "UPDATE POST-RESULTS STATE FOR NEXT TURN" in src, (
            "The end of text.complete must contain a block that updates the "
            "post-results state for the next turn — labeled 'UPDATE POST-RESULTS "
            "STATE FOR NEXT TURN'."
        )

    def test_combined_text_used_for_accumulation(self):
        src = _src()
        assert "combinedTextForResults" in src, (
            "Result-marker detection must use combined text "
            "(text + turnState.accumulatedText) so markers in both the new "
            "and accumulated text are detected."
        )

    def test_text_has_result_markers_called_in_text_complete(self):
        src = _src()
        assert re.search(
            r"textHasResultMarkers\s*\(\s*combinedTextForResults\s*\)",
            src,
        ), (
            "textHasResultMarkers(combinedTextForResults) must be called "
            "in the text.complete hook to detect result markers in the "
            "current response."
        )

    def test_last_turn_had_results_set_true_when_markers_found(self):
        src = _src()
        m = re.search(
            r"textHasResultMarkers[\s\S]{0,500}?lastTurnHadResults\s*:\s*true",
            src,
        )
        assert m, (
            "When textHasResultMarkers returns true, lastTurnHadResults must be "
            "set to true in the written state."
        )

    def test_last_turn_had_results_set_false_when_no_markers(self):
        src = _src()
        assert re.search(
            r"lastTurnHadResults\s*:\s*false",
            src,
        ), (
            "When textHasResultMarkers returns false, lastTurnHadResults must be "
            "set to false in the written state (else branch)."
        )


class TestInteractionWithExistingBlocks:
    """New checks must not break existing false-done and workflow blocks."""

    def test_new_check_before_short_false_done_check(self):
        src = _src()
        idxPost = src.find("POST-RESULTS TEXT-ONLY BLOCK")
        idxShort = src.find("Check short completion claims")
        assert idxPost >= 0, "POST-RESULTS TEXT-ONLY BLOCK section not found"
        assert idxShort >= 0, "Check short completion claims not found"
        assert idxPost < idxShort, (
            "Post-results text-only block must appear BEFORE the short "
            "completion claims check so mechanical blocks fire first."
        )

    def test_direct_false_done_still_present(self):
        src = _src()
        assert "COMPLETION_VERBATIM" in src, (
            "COMPLETION_VERBATIM false-done detection must still be present — "
            "new checks must not replace existing guardrails."
        )

    def test_qa_response_patterns_still_present(self):
        src = _src()
        assert "QA_RESPONSE_PATTERNS" in src, (
            "QA_RESPONSE_PATTERNS must still be present — new checks must "
            "not remove existing stop-pattern detection."
        )

    def test_has_local_work_block_still_present(self):
        src = _src()
        assert re.search(r"hasLocalWork.*text-only", src), (
            "The hasLocalWork text-only block must still be present — "
            "new checks add to, not replace, existing guardrails."
        )
