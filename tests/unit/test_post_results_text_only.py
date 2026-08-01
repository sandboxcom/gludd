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

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
def _src() -> str:
    return plugin_contract_source(PLUGIN)


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
            "readPostResultsState function must exist — reads the post-results state file for cross-turn memory."
        )

    def test_write_post_results_state_exists(self):
        src = _src()
        assert re.search(r"function\s+writePostResultsState\s*\(", src), (
            "writePostResultsState function must exist — persists the post-results state for the next turn."
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
            "textHasResultMarkers function must exist — detects subagent result markers in the agent's response text."
        )


class TestTextHasResultMarkers:
    """The result-marker detector must cover canonical result phrases."""

    def test_includes_task_result(self):
        src = _src()
        m = re.search(r"textHasResultMarkers[\s\S]{0,300}?\[[\s\S]{0,200}?\]", src, re.DOTALL)
        assert m, "textHasResultMarkers and its marker array not found"
        body = m.group(0).lower()
        assert "task result" in body or ('"task result"' in body), "Result marker array must include 'task result'."

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
            r"readPostResultsState\s*\(\s*\)[\s\S]{0,5000}?lastTurnHadResults",
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
            "The consecutive text-only block must exist — blocks the 2nd+ text-only response when work is pending."
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
        ), "The block must call recordBlock('consecutive-text-only')."

    def test_same_session_detection_uses_300s_window(self):
        src = _src()
        assert "300_000" in src, (
            "Text-only session detection must use a 300s (5min) window — sameSession = (now - lastTs) < 300_000."
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
        assert m, "When textHasResultMarkers returns true, lastTurnHadResults must be set to true in the written state."

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
            "QA_RESPONSE_PATTERNS must still be present — new checks must not remove existing stop-pattern detection."
        )

    def test_has_local_work_block_still_present(self):
        src = _src()
        assert re.search(r"hasLocalWork.*text-only", src), (
            "The hasLocalWork text-only block must still be present — "
            "new checks add to, not replace, existing guardrails."
        )


class TestConfiguredDispatchMinimum:
    """An explicit minimum blocks under-dispatch while the default stays adaptive."""

    def test_configured_minimum_block_exists(self):
        src = _src()
        assert "CONFIGURED MINIMUM" in src, (
            "enforce_stop_impl.ts must contain the opt-in configured-minimum "
            "block without presenting ten agents as a mandatory floor."
        )

    def test_dispatch_count_range_check(self):
        src = _src()
        assert "REQUIRED_AGENT_MIN > 0" in src
        m = re.search(
            r"turnState\.dispatchCount\s*>\s*0[\s\S]{0,300}"
            r"turnState\.dispatchCount\s*<\s*REQUIRED_AGENT_MIN",
            src,
        )
        assert m, (
            "The block must require an explicit minimum and compare the current "
            "dispatch count against that configured value."
        )

    def test_under_dispatch_floor_uses_record_block(self):
        src = _src()
        assert re.search(
            r'recordBlock\s*\(\s*["\']under-dispatch-floor["\']',
            src,
        ), "The block must call recordBlock('under-dispatch-floor')."

    def test_under_dispatch_floor_uses_record_blanked_response(self):
        src = _src()
        assert re.search(
            r'recordBlankedResponse\s*\(\s*["\']under-dispatch-floor["\']',
            src,
        ), "The block must call recordBlankedResponse('under-dispatch-floor')."

    def test_under_dispatch_floor_block_message_contains_configured_minimum(self):
        src = _src()
        assert "Configured minimum:" in src
        assert "String(REQUIRED_AGENT_MIN)" in src

    def test_under_dispatch_floor_block_message_contains_dispatch_count(self):
        src = _src()
        assert re.search(
            r"String\s*\(\s*turnState\.dispatchCount\s*\)"
            r"[\s\S]{0,500}Configured minimum:",
            src,
        ), "The block message must include the current count and configured minimum."

    def test_git_shipping_phrase_exemption(self):
        src = _src()
        m = re.search(
            r"GIT_SHIPPING_PHRASE[\s\S]+?release-cut",
            src,
        )
        assert m, "GIT_SHIPPING_PHRASE regex must exist for git-shipping exemption."
        body = m.group(0).lower()
        for phrase in ["ship-commit", "git-commit", "batch-push", "release-cut"]:
            assert phrase in body, (
                f"GIT_SHIPPING_PHRASE must include '{phrase}' — git operations "
                "may legitimately remain below an explicitly configured minimum."
            )

    def test_git_shipping_phrase_tested_before_block(self):
        src = _src()
        m = re.search(
            r"GIT_SHIPPING_PHRASE[\s\S]{0,500}recordBlock\s*\(.under-dispatch-floor",
            src,
        )
        assert m, (
            "GIT_SHIPPING_PHRASE must gate the under-dispatch block — "
            "the .test(text) call must precede recordBlock('under-dispatch-floor')."
        )

    def test_under_dispatch_floor_checks_pending_work(self):
        src = _src()
        assert "workState.hasPendingWork" in src or "forceDispatchDirective" in src, (
            "The under-dispatch-floor block must check workState.hasPendingWork — only fires when work is pending."
        )

    def test_under_dispatch_floor_exempts_has_work_artifact(self):
        src = _src()
        assert "hasWorkArtifact" in src, (
            "The under-dispatch-floor block must respect hasWorkArtifact — already-codified results should not block."
        )

    def test_under_dispatch_floor_before_consecutive_text_only(self):
        src = _src()
        idxUnder = src.find('recordBlock("under-dispatch-floor")')
        idxConsec = src.find("CONSECUTIVE TEXT-ONLY RESPONSES")
        assert idxUnder >= 0, "configured-minimum recordBlock call not found"
        assert idxConsec >= 0, "CONSECUTIVE TEXT-ONLY RESPONSES section not found"
        assert idxUnder < idxConsec, (
            "The configured-minimum block must appear before CONSECUTIVE "
            "TEXT-ONLY RESPONSES so the more specific check fires first."
        )


class TestWaveCompletionDetection:
    """Wave-completion detection: ≥3 result markers in one turn = lastTurnHadWave."""

    def test_wave_result_threshold_defined(self):
        src = _src()
        assert "WAVE_RESULT_THRESHOLD" in src, (
            "WAVE_RESULT_THRESHOLD must be defined — the threshold (≥3) at "
            "which a batch of subagent results counts as a 'wave'."
        )

    def test_wave_threshold_equals_3(self):
        src = _src()
        m = re.search(r"WAVE_RESULT_THRESHOLD\s*=\s*(\d+)", src)
        assert m, "WAVE_RESULT_THRESHOLD not found"
        val = int(m.group(1))
        assert val == 3, (
            f"WAVE_RESULT_THRESHOLD must be 3 (≥3 result markers = a wave), "
            f"got {val}. The incident was 7 subagent results landing at once."
        )

    def test_last_turn_had_wave_in_post_results_state(self):
        src = _src()
        assert "lastTurnHadWave" in src, (
            "PostResultsState must include lastTurnHadWave: boolean — "
            "tracks whether the previous turn had a full wave of results."
        )

    def test_wave_check_in_post_results_block(self):
        src = _src()
        assert re.search(
            r"postResultsState\.lastTurnHadWave\s*\)",
            src,
        ), (
            "The post-results text-only block must check lastTurnHadWave — "
            "both single results AND waves should trigger the block."
        )

    def test_last_result_count_in_state(self):
        src = _src()
        assert "lastResultCount" in src, (
            "PostResultsState must include lastResultCount: number — "
            "stores how many result markers were found for auditing."
        )

    def test_result_count_checked_against_wave_threshold(self):
        src = _src()
        assert re.search(
            r"resultCheck\.count\s*>=\s*WAVE_RESULT_THRESHOLD",
            src,
        ), (
            "The post-results state update must compare resultCheck.count against "
            "WAVE_RESULT_THRESHOLD to set lastTurnHadWave."
        )

    def test_text_has_result_markers_returns_count(self):
        src = _src()
        m = re.search(
            r"function\s+textHasResultMarkers[\s\S]{0,100}?\{",
            src,
        )
        assert m, "textHasResultMarkers function not found"
        body = src[m.start() : m.end() + 400]
        assert "count" in body, (
            "textHasResultMarkers must return { found, count } — the count is needed to determine wave completion."
        )

    def test_wave_block_message_mentions_wave(self):
        src = _src()
        if "lastTurnHadWave" in src:
            assert "wave" in src.lower(), (
                "The post-results block message must mention 'wave' when lastTurnHadWave is checked."
            )


# ── CHECKING_WHAT_LEFT_RE — Python-side regex mirror for structural matching ──
_CHECKING_WHAT_LEFT_PATTERN = re.compile(
    r"(?:let me\s+(?:just\s+)?(?:check|see|look|survey|find out)\s+"
    r"(?:what.?s?\s+(?:left|remaining|pending|still|else)"
    r"|how\s+much\s+(?:work|is left|remains)"
    r"|if.+work|whether.+work)"
    r"|i.?ll\s+(?:check|see|look)\s+(?:what.?s?\s+(?:left|remaining|pending)|how\s+much)"
    r"|(?:checking|seeing|looking|surveying)\s+"
    r"(?:what.?s?\s+(?:left|remaining|pending)|how\s+much)"
    r"|hold\s+on,?\s+let\s+me\s+check"
    r"|wait,?\s+let\s+me\s+check"
    r"|let\s+me\s+(?:first\s+)?(?:check|see|look)\s+(?:if|whether|what))",
    re.IGNORECASE,
)


class TestCheckingWhatLeftRe:
    """CHECKING_WHAT_LEFT_RE must be defined, used, match phrases, and use recordBlock."""

    def test_checking_what_left_re_defined(self):
        src = _src()
        assert "CHECKING_WHAT_LEFT_RE" in src, (
            "CHECKING_WHAT_LEFT_RE must be defined in enforce_stop_impl.ts — "
            "detects 'let me check what's left' and similar survey-before-action "
            "phrases that are stop-adjacent. Codified in AGENTS.md line 801."
        )

    def test_checking_what_left_re_used_in_text_complete(self):
        src = _src()
        assert re.search(
            r"CHECKING_WHAT_LEFT_RE\s*\.\s*test\s*\(\s*text\s*\)",
            src,
        ), (
            "CHECKING_WHAT_LEFT_RE.test(text) must be called in text.complete hook — "
            "only the actual invocation at block time counts as 'used'."
        )

    def test_checking_what_left_re_matches_common_phrases(self):
        matches = [
            "let me check what's left",
            "let me see what remains",
            "let me look what's pending",
            "checking what's left",
            "looking what's pending",
            "let me just check what's still left",
            "hold on, let me check",
            "wait, let me check",
            "let me first check whether",
            "I'll check what's pending",
            "let me check how much work is left",
        ]
        non_matches = [
            "dispatch subagents now",
            "fix the bug and commit",
            "running tests now",
            "committed abc1234, pushing",
            "let me fix that bug",
        ]
        for phrase in matches:
            assert _CHECKING_WHAT_LEFT_PATTERN.search(phrase), f"CHECKING_WHAT_LEFT_RE must match: {phrase!r}"
        for phrase in non_matches:
            assert not _CHECKING_WHAT_LEFT_PATTERN.search(phrase), f"CHECKING_WHAT_LEFT_RE must NOT match: {phrase!r}"

    def test_checking_what_left_block_uses_record_block(self):
        src = _src()
        assert re.search(
            r'recordBlock\s*\(\s*["\']checking-whats-left["\']',
            src,
        ), (
            "The checking-whats-left block must call recordBlock('checking-whats-left') "
            "so the block-reason audit file is machine-readable."
        )

    def test_checking_what_left_block_uses_log_false_done_block(self):
        src = _src()
        assert re.search(
            r"logFalseDoneBlock\s*\([^)]*['\"]checking-whats-left['\"]",
            src,
        ), (
            "The checking-whats-left block must call logFalseDoneBlock(..., 'checking-whats-left') "
            "so the false-done block audit file is machine-readable."
        )

    def test_checking_what_left_block_gated_by_disengage(self):
        src = _src()
        assert re.search(
            r"!\s*disengaged\s*&&\s*CHECKING_WHAT_LEFT_RE\s*\.\s*test\s*\(\s*text\s*\)",
            src,
        ), "The checking-whats-left block must gate on !disengaged — the disengage signal must bypass the check."

    def test_checking_what_left_block_gated_by_has_tool_call_intent(self):
        src = _src()
        assert re.search(
            r"CHECKING_WHAT_LEFT_RE\s*\.\s*test\s*\(\s*text\s*\)\s*&&\s*!\s*hasToolCallIntent",
            src,
        ), (
            "The checking-whats-left block must also check !hasToolCallIntent — "
            "if the response also has tool calls, it's not a pause."
        )

    def test_checking_what_left_block_clears_output(self):
        src = _src()
        m = re.search(
            r"CHECKING_WHAT_LEFT_RE[\s\S]{0,500}?clearBlockedOutput\s*\(\s*output\s*\)",
            src,
        )
        assert m, (
            "The checking-whats-left block must call clearBlockedOutput(output) — "
            "the outgoing text must be cleared before the block message is returned."
        )

    def test_checking_what_left_block_sets_turn_state_blocked(self):
        src = _src()
        m = re.search(
            r"CHECKING_WHAT_LEFT_RE[\s\S]{0,500}?turnState\s*\.\s*blocked\s*=\s*true",
            src,
        )
        assert m, (
            "The checking-whats-left block must set turnState.blocked = true "
            "so downstream checks know the response is already blocked."
        )
