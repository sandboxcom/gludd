"""Behavioral-invariant tests for enforce-floor.ts.

Covers plugin registration, key constants, SUBAGENT guard, floor/ceiling
band logic, tool.execute.before deny behavior, fail-open guarantees, and
disengage escape. Follows existing plugin test patterns (delegate, verified-claims).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
OPENCODE_JSON = ROOT / "opencode.json"


def _src(path: Path = PLUGIN_PATH) -> str:
    return path.read_text()


# ---------------------------------------------------------------------------
# Plugin file existence + opencode.json registration
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_file_exists(self):
        assert PLUGIN_PATH.is_file()

    def test_registered_in_opencode_json(self):
        raw = OPENCODE_JSON.read_text()
        assert "enforce-floor.ts" in raw, "enforce-floor.ts must be referenced in opencode.json"

    def test_exports_satisfies_plugin_type(self):
        src = _src()
        assert "satisfies Plugin" in src

    def test_export_is_async_factory(self):
        src = _src()
        assert "export default" in src
        assert "async" in src


# ---------------------------------------------------------------------------
# Key constants: FLOOR, CEILING, TARGET, MAX_STREAK, FLOOR_ENFORCE
# ---------------------------------------------------------------------------


class TestKeyConstants:
    def test_floor_default_is_7(self):
        src = _src()
        m = re.search(r'CLAUDE_AGENT_FLOOR",\s*"(\d+)"', src)
        assert m, "CLAUDE_AGENT_FLOOR default not found"
        assert m.group(1) == "7"

    def test_ceiling_default_is_10(self):
        src = _src()
        m = re.search(r'CLAUDE_AGENT_CEILING",\s*"(\d+)"', src)
        assert m, "CLAUDE_AGENT_CEILING default not found"
        assert m.group(1) == "10"

    def test_target_default_is_6(self):
        src = _src()
        m = re.search(r'CLAUDE_AGENT_TARGET\s*\|\|\s*"(\d+)"', src)
        assert m, "CLAUDE_AGENT_TARGET default not found"
        assert m.group(1) == "6"

    def test_target_capped_by_ceiling(self):
        src = _src()
        assert "Math.min" in src
        idx = src.find("const TARGET")
        after = src[idx:idx + 150]
        assert "CEILING" in after, "TARGET must be capped by CEILING via Math.min"

    def test_floor_enforce_is_hard_true(self):
        src = _src()
        assert "const FLOOR_ENFORCE = true" in src

    def test_max_streak_is_2(self):
        src = _src()
        assert "const MAX_STREAK = 2" in src

    def test_tunable_function_reads_override_file(self):
        src = _src()
        idx = src.find("function _tunable")
        after = src[idx:idx + 250]
        assert "readFileSync" in after
        assert "overridePath" in after
        assert "parseInt" in after

    def test_tunable_fail_open_on_bad_file(self):
        src = _src()
        idx = src.find("function _tunable")
        after = src[idx:idx + 400]
        assert "catch" in after.lower() or "} catch" in after

    def test_floor_override_file_path(self):
        src = _src()
        assert "/tmp/gludd-floor-override" in src

    def test_ceiling_override_file_path(self):
        src = _src()
        assert "/tmp/gludd-ceiling-override" in src


# ---------------------------------------------------------------------------
# SUBAGENT guard
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    def test_guard_checks_env_var(self):
        src = _src()
        assert 'process.env.OPENCODE_SUBAGENT === "1"' in src

    def test_guard_before_any_enforcement_in_before_hook(self):
        src = _src()
        idx = src.find('"tool.execute.before": async')
        assert idx > 0
        after = src[idx:]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        report_idx = after.find("_reportAlive")
        assert subagent_idx < report_idx, (
            "OPENCODE_SUBAGENT check must precede _reportAlive in tool.execute.before"
        )

    def test_guard_also_in_text_complete(self):
        src = _src()
        idx = src.find('"experimental.text.complete": async')
        assert idx > 0
        after = src[idx:]
        assert "OPENCODE_SUBAGENT" in after[:300], (
            "text.complete must also guard via OPENCODE_SUBAGENT"
        )


# ---------------------------------------------------------------------------
# Dispatch tool classification
# ---------------------------------------------------------------------------


class TestDispatchToolClassification:
    def test_task_is_dispatch_tool(self):
        src = _src()
        assert 'tool === "task"' in src

    def test_agent_is_dispatch_tool(self):
        src = _src()
        assert 'tool === "agent"' in src

    def test_workflow_is_dispatch_tool(self):
        src = _src()
        assert 'tool === "workflow"' in src

    def test_read_is_not_dispatch_tool(self):
        src = _src()
        assert 'toolName === "read"' in src

    def test_grep_is_not_dispatch_tool(self):
        src = _src()
        assert 'toolName === "grep"' in src

    def test_glob_is_not_dispatch_tool(self):
        src = _src()
        assert 'toolName === "glob"' in src

    def test_dispatch_resets_streak_counter(self):
        src = _src()
        idx = src.find("if (isDispatchTool(tool))")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "_streakCount = 0" in after
        assert "_readStreak = 0" in after

    def test_dispatch_resets_last_dispatch_ts(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        assert dispatch_idx > 0
        # Search for _lastDispatchTs = Date.now() starting FROM the dispatch branch
        after = src[dispatch_idx:dispatch_idx + 300]
        assert "_lastDispatchTs = Date.now()" in after, (
            "_lastDispatchTs must be reset inside dispatch branch"
        )


# ---------------------------------------------------------------------------
# Compulsive-check blocking (ANTI-LOOP)
# ---------------------------------------------------------------------------


class TestCompulsiveCheckBlocking:
    def test_regex_exists(self):
        src = _src()
        assert "COMPULSIVE_CHECK_RE" in src

    def test_blocks_git_log(self):
        src = _src()
        assert "git-log" in src

    def test_blocks_ci_verdict(self):
        src = _src()
        assert "ci-verdict" in src

    def test_blocks_git_diff(self):
        src = _src()
        idx = src.find("COMPULSIVE_CHECK_RE")
        after = src[idx:idx + 200]
        assert "git-diff" in after

    def test_block_message_mentions_loop_pattern(self):
        src = _src()
        idx = src.find("COMPULSIVE-CHECK LOOP BLOCKED")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "compulsive-check loop" in after.lower()

    def test_block_only_when_open_work_exists(self):
        src = _src()
        idx = src.find("COMPULSIVE_CHECK_RE.test")
        assert idx > 0
        after = src[idx:idx + 150]
        assert "openWorkExists" in after, "compulsive-check block must be gated on openWorkExists"


# ---------------------------------------------------------------------------
# Read-grinding detection
# ---------------------------------------------------------------------------


class TestReadGrinding:
    def test_separate_read_streak_counter(self):
        src = _src()
        assert "_readStreak" in src

    def test_read_grind_deny_threshold_10(self):
        src = _src()
        assert "_readStreak > 10" in src

    def test_read_grind_deny_time_60s(self):
        src = _src()
        assert "60_000" in src

    def test_read_grind_warn_threshold_5(self):
        src = _src()
        assert "_readStreak > 5" in src

    def test_read_grind_warn_time_30s(self):
        src = _src()
        assert "30_000" in src

    def test_read_grind_both_conditions_required_for_deny(self):
        src = _src()
        idx = src.find("_readStreak > 10")
        assert idx > 0
        after = src[idx:idx + 100]
        assert "&&" in after, "read-grind deny must use AND (both count AND time)"

    def test_read_grind_resets_on_dispatch(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx:dispatch_idx + 200]
        assert "_readStreak = 0" in after


# ---------------------------------------------------------------------------
# Message-shape enforcement (1-4 dispatch detection)
# ---------------------------------------------------------------------------


class TestMessageShapeEnforcement:
    def test_tracks_this_message_dispatch_count(self):
        src = _src()
        assert "_thisMessageDispatchCount" in src

    def test_tracks_prev_message_dispatch_count(self):
        src = _src()
        assert "_prevMessageDispatchCount" in src

    def test_block_when_prev_1_to_4(self):
        src = _src()
        idx = src.find("_prevMessageDispatchCount > 0")
        assert idx > 0
        after = src[idx:idx + 100]
        assert "4" in after or "5" in after

    def test_reset_on_text_complete(self):
        src = _src()
        idx = src.find('"experimental.text.complete"')
        assert idx > 0
        after = src[idx:]
        assert "_thisMessageDispatchCount = 0" in after[:2000]
        assert "_prevMessageDispatchCount = _thisMessageDispatchCount" in after[:2000]

    def test_block_message_mentions_message_shape_violation(self):
        src = _src()
        assert "MESSAGE-SHAPE VIOLATION" in src


# ---------------------------------------------------------------------------
# Floor/ceiling band enforcement logic (block on streak breach)
# ---------------------------------------------------------------------------


class TestFloorBreachBlock:
    def test_block_increments_streak_for_non_dispatch(self):
        src = _src()
        assert "_streakCount++" in src

    def test_block_only_when_streak_exceeds_max(self):
        src = _src()
        assert "_streakCount > MAX_STREAK" in src or "_streakCount <= MAX_STREAK" in src

    def test_block_only_when_open_work_exists(self):
        src = _src()
        # After streak check, openWorkExists gates the block
        streak_idx = src.find("_streakCount <= MAX_STREAK")
        if streak_idx > 0:
            after = src[streak_idx:streak_idx + 80]
            assert "openWorkExists" in after or "return" in after
        else:
            block_idx = src.find("if (!openWorkExists")
            assert block_idx > 0

    def test_block_sends_permission_decision_deny(self):
        src = _src()
        assert 'permissionDecision: "deny"' in src

    def test_block_message_includes_floor_and_target(self):
        src = _src()
        idx = src.find("AGENT-FLOOR BREACH")
        assert idx > 0
        after = src[idx:idx + 600]
        assert "FLOOR" in after
        assert "TARGET" in after

    def test_block_message_includes_dispatch_commands(self):
        src = _src()
        assert "DISPATCH COMMANDS" in src

    def test_block_streak_resets_when_no_open_work(self):
        src = _src()
        idx = src.find("_streakCount = 0")
        assert idx > 0

    def test_commit_tool_mode_propagates_to_open_work_probe(self):
        src = _src()
        assert "isCommitBashCommand" in src


# ---------------------------------------------------------------------------
# openWorkExists probes
# ---------------------------------------------------------------------------


class TestOpenWorkExists:
    def test_probes_ratchet_yml(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:idx + 400]
        assert "ratchet.yml" in after

    def test_probes_tasks_md(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert "TASKS.md" in after[:2000]

    def test_probes_bugs_md(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert "BUGS.md" in after[:2000]

    def test_probes_gate_status(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert ".gate-status" in after[:3500]

    def test_probes_git_status_porcelain(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert "git status --porcelain" in after or "git diff --name-only" in after

    def test_probes_todowrite_state_mirror(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert "gludd-todowrite-state.json" in after[:2000]

    def test_probes_mtime_based_dirty_detection(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert "mtimeMs" in after or "mtime" in after

    def test_probes_ci_status_cache(self):
        src = _src()
        idx = src.find("function openWorkExists")
        after = src[idx:]
        assert "gludd-watchdog-ci.json" in after[:3500]

    def test_function_returns_false_on_catch(self):
        src = _src()
        open_work_idx = src.find("function openWorkExists")
        end_of_fn = src.find("function _reportAlive", open_work_idx)
        fn_body = src[open_work_idx:end_of_fn]
        assert "return false" in fn_body, "openWorkExists must fall back to false on errors"


# ---------------------------------------------------------------------------
# Refill-state lifecycle (dispatch peak / drain detection)
# ---------------------------------------------------------------------------


class TestRefillState:
    def test_refill_threshold_is_3(self):
        src = _src()
        assert "const REFILL_THRESHOLD = 3" in src

    def test_peak_dispatch_is_5(self):
        src = _src()
        assert "const PEAK_DISPATCH = 5" in src

    def test_result_grace_calls_is_2(self):
        src = _src()
        assert "const RESULT_GRACE_CALLS = 2" in src

    def test_grace_check_returns_deny_not_pass_through(self):
        src = _src()
        idx = src.find("if (_resultProcessingGrace > 0)")
        assert idx > 0
        after = src[idx:idx + 500]
        assert 'permissionDecision: "deny"' in after, (
            "grace window must deny non-read non-dispatch calls, not silently allow"
        )

    def test_grace_deny_message_mentions_dispatch_gap(self):
        src = _src()
        assert "DISPATCH GAP" in src, "deny message must label the dispatch-gap pattern"

    def test_grace_deny_message_lists_allowed_tools(self):
        src = _src()
        idx = src.find("DISPATCH GAP")
        assert idx > 0
        after = src[idx:idx + 600]
        assert "task/agent dispatches" in after
        assert "read/grep/glob" in after

    def test_grace_deny_message_lists_forbidden_tools(self):
        src = _src()
        idx = src.find("DISPATCH GAP")
        assert idx > 0
        after = src[idx:idx + 800]
        assert "NO inline work" in after

    def test_grace_still_resets_streak_on_block(self):
        src = _src()
        # Find the SECOND occurrence — the original grace block, not the
        # post-result read limit block (which also checks _resultProcessingGrace).
        first = src.find("if (_resultProcessingGrace > 0)")
        assert first > 0
        second = src.find("if (_resultProcessingGrace > 0)", first + 1)
        assert second > 0
        after = src[second:second + 200]
        assert "_streakCount = 0" in after, (
            "grace block must still reset streak to prevent double-punishment"
        )

    def test_refill_flag_set_when_peak_exceeds_and_count_drops(self):
        src = _src()
        idx = src.find("function _updateRefillState")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "_dispatchPeak >= PEAK_DISPATCH" in after
        assert "_dispatchCount < REFILL_THRESHOLD" in after

    def test_refill_directive_injected_on_text_complete(self):
        src = _src()
        assert "REFILL NEEDED" in src

    def test_result_markers_exist(self):
        src = _src()
        assert "RESULT_MARKERS" in src
        assert "task result" in src

    def test_text_has_result_marker_function(self):
        src = _src()
        assert "function _textHasResultMarker" in src


# ---------------------------------------------------------------------------
# text.complete hook (floor breach text replacement)
# ---------------------------------------------------------------------------


class TestTextCompleteBehavior:
    def test_text_complete_hook_registered(self):
        src = _src()
        assert '"experimental.text.complete"' in src

    def test_text_complete_replaces_prose_on_floor_breach(self):
        src = _src()
        assert "FLOOR BREACH — TEXT RESPONSE REPLACED" in src

    def test_text_complete_increments_fire_counter(self):
        src = _src()
        idx = src.find("GLUDD_FLOOR_TEXT_COMPLETE_COUNT")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "writeFileSync" in after

    def test_text_complete_fail_open_on_error(self):
        src = _src()
        idx = src.find('"experimental.text.complete"')
        assert idx > 0
        # Find the outer try block in text.complete and verify its catch returns output
        after = src[idx:]
        try_idx = after.find("try {")
        assert try_idx > 0
        # The outer catch is the one after the main try body — search for "return output"
        # near "console.error" which is in the outer catch
        error_idx = after.find("console.error")
        assert error_idx > 0, "enforce-floor text.complete error logging must exist"
        catch_region = after[error_idx:error_idx + 100]
        assert "return output" in catch_region, (
            "text.complete outer catch must return output (fail-open)"
        )


# ---------------------------------------------------------------------------
# session.idle hook
# ---------------------------------------------------------------------------


class TestSessionIdleHook:
    def test_session_idle_hook_registered(self):
        src = _src()
        assert '"session.idle"' in src

    def test_session_idle_resets_message_shape_counters(self):
        src = _src()
        idx = src.find('"session.idle"')
        after = src[idx:]
        assert "_thisMessageDispatchCount = 0" in after[:800]
        assert "_thisMessageTotalCalls = 0" in after[:800]

    def test_session_idle_resets_read_streak(self):
        src = _src()
        idx = src.find('"session.idle"')
        after = src[idx:]
        assert "_readStreak = 0" in after[:600]

    def test_session_idle_resets_result_grace(self):
        src = _src()
        idx = src.find('"session.idle"')
        after = src[idx:]
        assert "_resultProcessingGrace = 0" in after[:600]


# ---------------------------------------------------------------------------
# Disengage escape hatch
# ---------------------------------------------------------------------------


class TestDisengage:
    def test_disengage_file_referenced(self):
        src = _src()
        assert "gludd-watchdog-disengage.json" in src

    def test_disengage_hoisted_above_read_grind(self):
        src = _src()
        idx = src.find("disengagedEarly")
        assert idx > 0
        after = src[idx:idx + 5000]
        read_grind_idx = after.find("_readStreak++")
        assert read_grind_idx > 0
        # _readStreak++ appears after disengagedEarly check — disengage is hoisted first
        assert after.find("disengagedEarly") < read_grind_idx

    def test_disengage_clamped_to_max_one_hour(self):
        src = _src()
        assert "3_600_000" in src

    def test_disengage_resets_streak_to_zero(self):
        src = _src()
        # Three disengage points all reset streak
        streak_resets = [m.start() for m in re.finditer(r"_streakCount\s*=\s*0", src)]
        msg = f"Expected >=3 streak resets (one per disengage point), found {len(streak_resets)}"
        assert len(streak_resets) >= 3, msg

    def test_disengage_skips_all_blocks(self):
        src = _src()
        assert src.count("gludd-watchdog-disengage.json") >= 3, (
            "disengage file must be checked at multiple enforcement points"
        )


# ---------------------------------------------------------------------------
# Shared streak state (cross-plugin co-ordination)
# ---------------------------------------------------------------------------


class TestSharedStreakState:
    def test_shared_streak_file_path(self):
        src = _src()
        assert "/tmp/gludd-tool-streak.json" in src

    def test_shared_streak_interface_fields(self):
        src = _src()
        idx = src.find("interface SharedStreakState")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "streak" in after
        assert "lastDispatchTs" in after
        assert "lastWriter" in after
        assert "pid" in after

    def test_stale_streak_auto_resets_after_60s(self):
        src = _src()
        idx = src.find("STALE_MS")
        assert idx > 0
        assert "60_000" in src

    def test_cross_session_pid_guard(self):
        src = _src()
        idx = src.find("pid-reset")
        assert idx > 0

    def test_dedup_window_prevents_double_count(self):
        src = _src()
        assert "STREAK_DEDUP_WINDOW_MS" in src
        assert "500" in src

    def test_update_function_exists(self):
        src = _src()
        assert "function updateSharedStreak" in src

    def test_read_function_exists(self):
        src = _src()
        assert "function readSharedStreak" in src


# ---------------------------------------------------------------------------
# Heartbeat and liveness reporting
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_report_alive_function_exists(self):
        src = _src()
        assert "function _reportAlive" in src

    def test_report_alive_writes_to_alive_file(self):
        src = _src()
        idx = src.find("function _reportAlive")
        after = src[idx:idx + 200]
        assert "gludd-plugin-alive.json" in after

    def test_heartbeat_writes_per_plugin_file(self):
        src = _src()
        idx = src.find("function _writeHeartbeat")
        assert idx > 0
        after = src[idx:idx + 300]
        assert "gludd-plugin-heartbeat-enforce-floor.json" in after

    def test_plugin_loaded_logging(self):
        src = _src()
        assert "gludd-plugin-loaded.log" in src


# ---------------------------------------------------------------------------
# Fail-open guarantees: catch blocks, no unguarded throw
# ---------------------------------------------------------------------------


class TestFailOpenGuarantee:
    def test_fail_open_comment_in_file(self):
        src = _src()
        assert "fail open" in src.lower() or "fail-open" in src.lower()

    def test_all_catch_blocks_present(self):
        src = _src()
        catch_count = len(re.findall(r"\bcatch\b", src))
        assert catch_count >= 10, f"expected >=10 catch blocks, found {catch_count}"

    def test_tool_execute_before_catch_surrounds_all_enforcement(self):
        src = _src()
        before_idx = src.find('"tool.execute.before"')
        after = src[before_idx:]
        # The outer try/catch in tool.execute.before
        m = re.search(r"try\s*{.*?}\s*catch\s*{", after, re.DOTALL)
        assert m, "tool.execute.before must have outer try/catch for fail-open"

    def test_text_complete_returns_output_on_error(self):
        src = _src()
        idx = src.find('"experimental.text.complete"')
        after = src[idx:]
        # Should have a catch that returns output
        m = re.search(
            r"}\s*catch\s*\([^)]*\)\s*{.*?return output", after, re.DOTALL
        )
        assert m, "text.complete catch must return output (fail-open)"

    def test_default_no_force_throw(self):
        src = _src()
        assert "throw new Error" not in src, "plugin must not throw uncaught errors"


# ---------------------------------------------------------------------------
# Plugin returns all three hooks
# ---------------------------------------------------------------------------


class TestPluginHookRegistration:
    def test_returns_tool_execute_before(self):
        src = _src()
        assert '"tool.execute.before"' in src

    def test_returns_session_idle(self):
        src = _src()
        assert '"session.idle"' in src

    def test_returns_experimental_text_complete(self):
        src = _src()
        assert '"experimental.text.complete"' in src


# ---------------------------------------------------------------------------
# Startup stale-state cleanup
# ---------------------------------------------------------------------------


class TestPostResultReadLimit:
    """Post-result read enforcement (2026-07-12 — close "reads are free" gap)."""

    def test_post_result_read_limit_constant_exists(self):
        src = _src()
        assert "POST_RESULT_READ_LIMIT" in src

    def test_post_result_read_limit_value_is_3(self):
        src = _src()
        assert "POST_RESULT_READ_LIMIT = 3" in src

    def test_consecutive_reads_counter_exists(self):
        src = _src()
        assert "_consecutiveReadsAfterResults" in src

    def test_post_result_read_deny_message_exists(self):
        src = _src()
        assert "POST-RESULT READ LIMIT EXCEEDED" in src

    def test_deny_message_mentions_dispatch_gap(self):
        src = _src()
        idx = src.find("POST-RESULT READ LIMIT EXCEEDED")
        assert idx > 0
        after = src[idx:idx + 500]
        assert "dispatch-gap" in after.lower()

    def test_deny_message_requires_dispatch(self):
        src = _src()
        idx = src.find("POST-RESULT READ LIMIT EXCEEDED")
        assert idx > 0
        after = src[idx:idx + 600]
        assert "Dispatch task/agent" in after

    def test_counter_resets_on_dispatch(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        assert dispatch_idx > 0
        after = src[dispatch_idx:dispatch_idx + 500]
        assert "_consecutiveReadsAfterResults = 0" in after, (
            "_consecutiveReadsAfterResults must reset inside dispatch branch"
        )

    def test_counter_resets_on_result_detection(self):
        src = _src()
        idx = src.find("_resultProcessingGrace = RESULT_GRACE_CALLS")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "_consecutiveReadsAfterResults = 0" in after, (
            "_consecutiveReadsAfterResults must reset when results detected"
        )

    def test_counter_resets_in_session_idle(self):
        src = _src()
        idx = src.find('"session.idle"')
        assert idx > 0
        after = src[idx:idx + 800]
        assert "_consecutiveReadsAfterResults = 0" in after, (
            "_consecutiveReadsAfterResults must reset in session.idle"
        )

    def test_block_gated_on_result_grace_active(self):
        src = _src()
        idx = src.find("_resultProcessingGrace > 0")
        assert idx > 0
        after = src[idx:idx + 100]
        assert "_consecutiveReadsAfterResults++" in after, (
            "read counter increment must be gated on _resultProcessingGrace > 0"
        )

    def test_block_uses_post_result_read_limit_threshold(self):
        src = _src()
        idx = src.find("_consecutiveReadsAfterResults > POST_RESULT_READ_LIMIT")
        assert idx > 0, "deny must check against POST_RESULT_READ_LIMIT, not a magic number"

    def test_block_is_hard_deny_not_advisory(self):
        src = _src()
        idx = src.find("POST-RESULT READ LIMIT EXCEEDED")
        assert idx > 0
        before = src[idx - 400:idx]
        assert 'permissionDecision: "deny"' in before, (
            "post-result read limit must be a hard deny (permissionDecision), not advisory console.warn"
        )


# ---------------------------------------------------------------------------
# Stale-state cleanup on startup
# ---------------------------------------------------------------------------


class TestStartupCleanup:
    def test_read_grind_stale_reset_on_load(self):
        src = _src()
        assert "gludd-read-grind.json" in src

    def test_stale_reset_uses_atomic_tmp_rename(self):
        src = _src()
        # Look in the stale-reset block near the factory start
        factory_idx = src.find("export default")
        factory_section = src[factory_idx:factory_idx + 500]
        # The rename in the cleanup
        assert "renameSync" in factory_section or "renameSync" in src
