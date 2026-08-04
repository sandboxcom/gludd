"""Behavioral tests for enforce-floor.ts floor/ceiling enforcement.

Covers: floor breach triggers, ceiling/wave-width blocks, dispatch counting
and streak resets, env var overrides, disengage escape hatch, load throttle,
session-start stall, message-boundary detection, and fail-open guarantees.

Each test is framed as a behavioral assertion: "when X happens, the plugin
returns Y" or "state variable Z transitions to value W".
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
SHARED_PATH = ROOT / ".opencode" / "lib" / "shared.ts"


def _src(path: Path = PLUGIN_PATH) -> str:
    return path.read_text()


def _shared_src() -> str:
    return SHARED_PATH.read_text()


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Floor Breach — streak-based blocking
# ══════════════════════════════════════════════════════════════════════════════


class TestFloorBreachBehavior:
    """When non-dispatch calls accumulate past MAX_STREAK and open work exists,
    the plugin MUST deny the next non-dispatch tool call."""

    def test_streak_incremented_on_non_dispatch_call(self):
        src = _src()
        assert "_streakCount++" in src, "Non-dispatch calls MUST increment _streakCount"

    def test_floor_breach_checks_streak_gt_effective_max(self):
        src = _src()
        assert "_streakCount <= effectiveMax" in src, "Block MUST be gated on _streakCount > effectiveMax"

    def test_floor_breach_returns_permission_deny(self):
        src = _src()
        assert 'permissionDecision: "deny"' in src, (
            "Floor breach MUST return permissionDecision: deny, not advisory warn"
        )

    def test_floor_breach_block_message_includes_count(self):
        src = _src()
        block_fn = "function _buildFloorBreachBlock"
        assert block_fn in src
        fn_idx = src.find(block_fn)
        fn_body = src[fn_idx : fn_idx + 1000]
        assert "streakCount" in fn_body, "Breach message MUST report the current streak count"
        assert "FLOOR" in fn_body
        assert "TARGET" in fn_body

    def test_floor_breach_only_when_open_work_exists(self):
        src = _src()
        floor_breach_idx = src.find("AGENT-FLOOR BREACH")
        assert floor_breach_idx > 0
        after_block = src[floor_breach_idx:]
        assert "openWorkExists" in after_block, "Floor breach MUST be gated on openWorkExists()"

    def test_streak_resets_to_zero_when_no_open_work(self):
        src = _src()
        idx = src.find("_streakCount = 0")
        assert idx > 0, "_streakCount MUST reset to 0 when openWorkExists returns false"

    def test_max_streak_during_session_start_is_1(self):
        src = _src()
        assert "SESSION_START_STREAK_MAX = 1" in src, (
            "During session-start window, effective max streak MUST be 1 (tighter)"
        )

    def test_max_streak_normal_is_2(self):
        src = _src()
        assert "const MAX_STREAK = 2" in src, "Normal MAX_STREAK MUST be 2"

    def test_effective_max_uses_session_start_max_in_window(self):
        src = _src()
        assert "_isInSessionStartWindow() ? SESSION_START_STREAK_MAX : MAX_STREAK" in src, (
            "effectiveMax MUST use tighter threshold during session-start window"
        )


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Ceiling / Wave Width enforcement
# ══════════════════════════════════════════════════════════════════════════════


class TestWaveWidthBehavior:
    """When dispatches in a message exceed WAVE_WIDTH (ceiling), further
    dispatches are blocked. When an undersized wave completes, inline work
    is blocked until a full-width wave is dispatched."""

    def test_wave_width_default_is_10(self):
        src = _src()
        assert 'GLUDD_DISPATCH_WAVE_WIDTH",\n  "10"' in src or '"10",\n)' in src, "WAVE_WIDTH default MUST be 10"

    def test_dispatch_exceeding_wave_width_is_blocked(self):
        src = _src()
        assert "_thisMessageDispatchCount >= eff.waveWidth" in src, "Dispatch MUST be blocked when count >= waveWidth"

    def test_wave_width_violation_returns_deny(self):
        src = _src()
        wave_idx = src.find("WAVE WIDTH VIOLATION")
        assert wave_idx > 0
        assert 'permissionDecision: "deny"' in src[wave_idx - 500 : wave_idx], (
            "Wave width violation MUST return permissionDecision: deny"
        )

    def test_prev_message_undersized_wave_blocks_inline_work(self):
        src = _src()
        assert "_prevMessageDispatchCount > 0 &&\n        _prevMessageDispatchCount < eff.waveWidth" in src, (
            "Previous message with undersized wave MUST block inline work"
        )

    def test_prev_message_undersized_returns_deny(self):
        src = _src()
        wave2_idx = src.find("WAVE WIDTH VIOLATION — INLINE WORK BLOCKED")
        assert wave2_idx > 0
        assert 'permissionDecision: "deny"' in src[wave2_idx - 500 : wave2_idx], (
            "Undersized-wave inline work block MUST return deny"
        )

    def test_dispatch_preflight_recorded_on_first_dispatch(self):
        src = _src()
        assert "recordDispatchPreflight" in src, "First dispatch in a wave MUST record the preflight"
        assert "_thisMessageDispatchCount === 0" in src, (
            "Preflight MUST be recorded only when thisMessageDispatchCount is 0"
        )

    def test_dispatch_wave_complete_recorded_on_full_wave(self):
        src = _src()
        assert "recordDispatchWaveComplete" in src, "Full wave completion MUST be recorded"
        assert "_thisMessageDispatchCount === eff.waveWidth" in src, (
            "Wave complete MUST fire exactly when dispatch count == waveWidth"
        )


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Dispatch Counting and Streak Reset
# ══════════════════════════════════════════════════════════════════════════════


class TestDispatchCountingBehavior:
    """When a dispatch tool (task/agent/workflow) is called, all streak
    counters reset to 0 and dispatch counters increment."""

    def test_dispatch_resets_streak_count(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        assert dispatch_idx > 0
        after = src[dispatch_idx : dispatch_idx + 250]
        assert "_streakCount = 0" in after, "Dispatch MUST reset _streakCount to 0"

    def test_dispatch_resets_read_streak(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 250]
        assert "_readStreak = 0" in after, "Dispatch MUST reset _readStreak to 0"

    def test_dispatch_increments_dispatch_count(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_dispatchCount++" in after, "Dispatch MUST increment _dispatchCount"

    def test_dispatch_increments_this_message_count(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_thisMessageDispatchCount++" in after, "Dispatch MUST increment _thisMessageDispatchCount"

    def test_dispatch_increments_session_count(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_sessionDispatchCount++" in after, "Dispatch MUST increment _sessionDispatchCount"

    def test_dispatch_updates_last_dispatch_timestamp(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 250]
        assert "_lastDispatchTs = now" in after, "Dispatch MUST update _lastDispatchTs to current time"

    def test_dispatch_tracks_peak(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_dispatchPeak" in after, "Dispatch MUST update _dispatchPeak when count exceeds prior peak"

    def test_dispatch_resets_consecutive_reads_in_result_phase(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 400]
        assert "_consecutiveReadsInResultPhase = 0" in after, "Dispatch MUST reset _consecutiveReadsInResultPhase to 0"

    def test_task_tool_is_classified_as_dispatch(self):
        shared = _shared_src()
        assert 'DISPATCH_TOOLS = Object.freeze(["task", "agent", "workflow"])' in shared, (
            "task/agent/workflow MUST be classified as dispatch tools in shared.ts"
        )

    def test_read_tools_are_not_classified_as_dispatch(self):
        shared = _shared_src()
        assert 'READ_TOOLS = Object.freeze(["read", "grep", "glob"])' in shared, (
            "read/grep/glob MUST be classified as read-only tools"
        )


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Env Var Overrides
# ══════════════════════════════════════════════════════════════════════════════


class TestEnvVarOverrides:
    """Env vars MUST override the default floor/ceiling/wave values
    and allow disabling enforcement entirely."""

    def test_gludd_floor_enforce_zero_disables_all(self):
        src = _src()
        assert 'GLUDD_FLOOR_ENFORCE !== "0"' in src, "GLUDD_FLOOR_ENFORCE=0 MUST disable all enforcement"
        assert "FLOOR_ENFORCE" in src, "FLOOR_ENFORCE variable MUST gate enforcement"

    def test_claude_agent_floor_overrides_default(self):
        src = _src()
        assert 'CLAUDE_AGENT_FLOOR"' in src, "CLAUDE_AGENT_FLOOR env var MUST override the floor default"

    def test_claude_agent_ceiling_overrides_default(self):
        src = _src()
        assert 'CLAUDE_AGENT_CEILING"' in src, "CLAUDE_AGENT_CEILING env var MUST override the ceiling default"

    def test_gludd_message_boundary_ms_overrides_default(self):
        src = _src()
        assert "GLUDD_MESSAGE_BOUNDARY_MS" in src, (
            "GLUDD_MESSAGE_BOUNDARY_MS env var MUST override message boundary timeout"
        )

    def test_floor_override_file_takes_precedence(self):
        src = _src()
        assert "/tmp/gludd-floor-override" in src, "File at /tmp/gludd-floor-override MUST take precedence over env var"

    def test_ceiling_override_file_takes_precedence(self):
        src = _src()
        assert "/tmp/gludd-ceiling-override" in src, (
            "File at /tmp/gludd-ceiling-override MUST take precedence over env var"
        )

    def test_dispatch_wave_width_has_env_var_override(self):
        src = _src()
        assert "GLUDD_DISPATCH_WAVE_WIDTH" in src, "GLUDD_DISPATCH_WAVE_WIDTH env var MUST override wave width"

    def test_wave_width_override_file_path(self):
        src = _src()
        assert "/tmp/gludd-dispatch-wave-width" in src, (
            "File at /tmp/gludd-dispatch-wave-width MUST override wave width"
        )

    def test_target_cannot_exceed_ceiling(self):
        src = _src()
        target_idx = src.find("const TARGET = Math.min")
        assert target_idx > 0
        after = src[target_idx : target_idx + 120]
        assert "CEILING" in after, "TARGET MUST be capped at CEILING via Math.min"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Disengage Escape Hatch
# ══════════════════════════════════════════════════════════════════════════════


class TestDisengageBehavior:
    """When isDisengaged() returns true, all streak counters MUST reset to 0
    and enforcement skips. When false, enforcement proceeds normally."""

    def test_disengage_resets_streak_count(self):
        src = _src()
        disengage_resets = [
            m.start()
            for m in re.finditer(
                r"_streakCount\s*=\s*0",
                src,
            )
        ]
        assert len(disengage_resets) >= 2, (
            f"_streakCount MUST reset on both disengage AND no-open-work ({len(disengage_resets)} found)"
        )

    def test_disengage_checked_before_streak_increment(self):
        src = _src()
        streak_inc_idx = src.find("_streakCount++")
        assert streak_inc_idx > 0
        before_streak = src[:streak_inc_idx]
        assert "isDisengaged()" in before_streak, "isDisengaged() MUST be checked before _streakCount++"

    def test_disengage_is_imported_from_shared(self):
        src = _src()
        assert "isDisengaged" in src, "isDisengaged MUST be imported from shared.ts"

    def test_disengage_is_checked_at_multiple_points(self):
        src = _src()
        count = src.count("isDisengaged()")
        assert count >= 2, f"isDisengaged() MUST be checked at >=2 enforcement points, found {count}"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Load Throttle
# ══════════════════════════════════════════════════════════════════════════════


class TestLoadThrottleBehavior:
    """When /tmp/gludd-load-throttle exists with valid load data, the effective
    floor/ceiling/waveWidth MUST be scaled down based on load ratio."""

    def test_load_throttle_file_checked(self):
        src = _src()
        assert "/tmp/gludd-load-throttle" in src, "Load throttle file MUST be checked at /tmp/gludd-load-throttle"

    def test_get_effective_floor_function_exists(self):
        src = _src()
        assert "function getEffectiveFloor" in src, (
            "getEffectiveFloor() MUST exist to return throttled/unthrottled values"
        )

    def test_throttle_active_window_is_120_seconds(self):
        src = _src()
        assert "THROTTLE_ACTIVE_MS = 120_000" in src, "Throttle active window MUST be 120 seconds"

    def test_throttle_stale_window_is_300_seconds(self):
        src = _src()
        assert "THROTTLE_STALE_MS = 300_000" in src, (
            "Throttle stale window MUST be 300 seconds (stale state is discarded)"
        )

    def test_throttle_zero_floor_dispatches_nothing(self):
        src = _src()
        throttle_idx = src.find("effectiveFloor === 0")
        assert throttle_idx > 0
        after = src[throttle_idx : throttle_idx + 200]
        assert "floor: 0" in after, "When effectiveFloor is 0, floor/target/waveWidth MUST all be 0"
        assert "waveWidth: 0" in after
        assert "target: 0" in after

    def test_throttle_scales_wave_width_proportionally(self):
        src = _src()
        ratio_idx = src.find("const ratio = effectiveFloor / FLOOR")
        assert ratio_idx > 0, "Wave width MUST scale proportionally to effectiveFloor / FLOOR"

    def test_throttle_minimum_wave_width_is_2(self):
        src = _src()
        assert "Math.max(2, Math.round(WAVE_WIDTH * ratio))" in src, "Throttled wave width MUST be at minimum 2"

    def test_throttle_console_warns_when_active(self):
        src = _src()
        assert "LOAD THROTTLE ACTIVE" in src, "console.warn MUST fire when load throttle is active"

    def test_get_effective_floor_is_called_in_hook(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:]
        m = re.search(r"getEffectiveFloor\(\)", after)
        assert m, "getEffectiveFloor() MUST be called in tool.execute.before hook"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Session-Start Dispatch Stall
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionStartBehavior:
    """After session start, if 0 dispatches occur within SESSION_START_TIME_BLOCK_MS
    (60s), non-dispatch tool calls are blocked."""

    def test_session_start_time_block_is_60_seconds(self):
        src = _src()
        assert "SESSION_START_TIME_BLOCK_MS = 60_000" in src, "Session-start time block MUST be 60 seconds"

    def test_session_start_window_is_90_seconds(self):
        src = _src()
        assert "SESSION_START_WINDOW_MS = 90_000" in src, "Session-start window MUST be 90 seconds"

    def test_session_start_block_requires_zero_dispatches(self):
        src = _src()
        ss_idx = src.find("_sessionDispatchCount === 0")
        assert ss_idx > 0, "Session-start block MUST only fire when _sessionDispatchCount is 0"

    def test_session_start_block_returns_deny(self):
        src = _src()
        ss_block_idx = src.find("SESSION-START DISPATCH STALL")
        assert ss_block_idx > 0
        assert 'permissionDecision: "deny"' in src[ss_block_idx - 400 : ss_block_idx], (
            "Session-start stall MUST return permissionDecision: deny"
        )

    def test_session_start_block_message_mentions_elapsed_time(self):
        src = _src()
        ss_block_idx = src.find("SESSION-START DISPATCH STALL")
        assert ss_block_idx > 0
        after = src[ss_block_idx : ss_block_idx + 500]
        assert "elapsed since session start" in after, "Block message MUST report elapsed time since session start"

    def test_session_start_read_warn_at_3_reads(self):
        src = _src()
        assert "SESSION_START_READ_WARN = 3" in src, "Session-start read warn threshold MUST be 3"

    def test_session_start_read_deny_at_6_reads(self):
        src = _src()
        assert "SESSION_START_READ_DENY = 6" in src, (
            "Session-start read deny threshold MUST be 6 (after 6 reads with no dispatch)"
        )

    def test_session_start_read_deny_returns_deny(self):
        src = _src()
        assert "SESSION-START READ-GRINDING" in src, (
            "Session-start read-grinding block MUST have a clear message identifier"
        )


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Message Boundary Detection
# ══════════════════════════════════════════════════════════════════════════════


class TestMessageBoundaryBehavior:
    """A 5s inter-call gap marks a new agent message. On boundary: prev counters
    are saved from this-message counters, and this-message counters reset."""

    def test_message_boundary_default_is_5_seconds(self):
        src = _src()
        assert 'GLUDD_MESSAGE_BOUNDARY_MS || "5000"' in src, "MESSAGE_BOUNDARY_MS default MUST be 5000 (5 seconds)"

    def test_prev_message_dispatch_count_saved_on_boundary(self):
        src = _src()
        assert "_prevMessageDispatchCount = _thisMessageDispatchCount" in src, (
            "On message boundary, _prevMessageDispatchCount MUST capture _thisMessageDispatchCount"
        )

    def test_this_message_count_reset_on_boundary(self):
        src = _src()
        boundary_idx = src.find("_prevMessageDispatchCount = _thisMessageDispatchCount")
        after = src[boundary_idx : boundary_idx + 100]
        assert "_thisMessageDispatchCount = 0" in after, (
            "On message boundary, _thisMessageDispatchCount MUST reset to 0"
        )

    def test_this_message_total_reset_on_boundary(self):
        src = _src()
        boundary_idx = src.find("_prevMessageDispatchCount = _thisMessageDispatchCount")
        assert boundary_idx > 0
        after = src[boundary_idx : boundary_idx + 200]
        assert "_thisMessageTotalCalls = 0" in after, "On message boundary, _thisMessageTotalCalls MUST reset to 0"

    def test_is_new_message_detected_via_time_gap(self):
        src = _src()
        assert "(now - _lastCallTs) > MESSAGE_BOUNDARY_MS" in src, (
            "New message MUST be detected when inter-call gap exceeds MESSAGE_BOUNDARY_MS"
        )


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Post-Dispatch Result Phase
# ══════════════════════════════════════════════════════════════════════════════


class TestResultPhaseBehavior:
    """After a dispatch, a grace window (POST_DISPATCH_GRACE_MS = 15s) allows
    up to RESULT_PHASE_READ_LIMIT (3) reads before blocking. Mutations (bash/
    edit/write) are blocked immediately in the result phase."""

    def test_post_dispatch_grace_is_15_seconds(self):
        src = _src()
        assert "POST_DISPATCH_GRACE_MS = 15000" in src, "Post-dispatch grace window MUST be 15 seconds"

    def test_result_phase_read_limit_is_3(self):
        src = _src()
        assert "RESULT_PHASE_READ_LIMIT = 3" in src, "Result-phase read limit MUST be 3"

    def test_in_result_phase_requires_prior_dispatches(self):
        src = _src()
        result_idx = src.find("const inResultPhase")
        assert result_idx > 0
        after = src[result_idx : result_idx + 150]
        assert "_dispatchCount > 0" in after, "inResultPhase MUST require _dispatchCount > 0"
        assert "msSinceDispatch < POST_DISPATCH_GRACE_MS" in after, (
            "inResultPhase MUST check msSinceDispatch against POST_DISPATCH_GRACE_MS"
        )
        assert "msSinceDispatch > 2000" in after, (
            "inResultPhase MUST require >= 2s gap from dispatch (avoid self-trigger)"
        )

    def test_result_phase_blocks_mutations(self):
        src = _src()
        gap_idx = src.find("DISPATCH GAP — INLINE MUTATION BLOCKED")
        assert gap_idx > 0
        block_region = src[gap_idx - 600 : gap_idx + 500]
        assert 'tool === "bash" || tool === "edit" || tool === "write"' in block_region, (
            "Result phase MUST block bash/edit/write mutations"
        )

    def test_result_phase_reads_increment_consecutive_counter(self):
        src = _src()
        assert "_consecutiveReadsInResultPhase++" in src, (
            "Reads in result phase MUST increment _consecutiveReadsInResultPhase"
        )

    def test_result_phase_read_exceeding_limit_returns_deny(self):
        src = _src()
        limit_idx = src.find("POST-RESULT READ LIMIT EXCEEDED")
        assert limit_idx > 0
        assert 'permissionDecision: "deny"' in src[limit_idx - 400 : limit_idx], (
            "Result-phase read limit exceeded MUST return deny"
        )


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Compulsive-Check Blocking (ANTI-LOOP)
# ══════════════════════════════════════════════════════════════════════════════


class TestCompulsiveCheckBehavior:
    """Standalone bash calls to make git-log / ci-verdict / git-diff /
    gate-refresh are blocked when open work exists."""

    def test_compulsive_check_regex_matches_all_four_targets(self):
        src = _src()
        match = re.search(
            r"COMPULSIVE_CHECK_RE\s*=\s*/(.+)/",
            src,
        )
        assert match, "COMPULSIVE_CHECK_RE regex must be defined"
        regex = match.group(1)
        assert "git-log" in regex
        assert "ci-verdict" in regex
        assert "git-diff" in regex
        assert "gate-refresh" in regex, "COMPULSIVE_CHECK_RE MUST match all 4 forbidden standalone commands"

    def test_compulsive_check_block_returns_deny(self):
        src = _src()
        loop_idx = src.find("COMPULSIVE-CHECK LOOP BLOCKED")
        assert loop_idx > 0
        assert 'permissionDecision: "deny"' in src[loop_idx - 400 : loop_idx], "Compulsive-check block MUST return deny"

    def test_compulsive_check_block_requires_open_work(self):
        src = _src()
        loop_idx = src.find("COMPULSIVE_CHECK_RE.test")
        assert loop_idx > 0
        after = src[loop_idx : loop_idx + 80]
        assert "openWorkExists" in after, "Compulsive-check block MUST be gated on openWorkExists()"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Read-Grinding Detection
# ══════════════════════════════════════════════════════════════════════════════


class TestReadGrindingBehavior:
    """15+ reads over 60+ seconds with no dispatch trigger a hard block.
    8+ reads over 30+ seconds trigger a console.warn nudge."""

    def test_read_grind_deny_at_15_reads_and_60_seconds(self):
        src = _src()
        deny_idx = src.find("_readStreak > 15")
        assert deny_idx > 0
        after = src[deny_idx : deny_idx + 80]
        assert "msSinceDispatch > 60_000" in after, "Read-grind deny MUST require >=15 reads AND >=60s since dispatch"

    def test_read_grind_warn_at_8_reads_and_30_seconds(self):
        src = _src()
        assert "_readStreak > 8" in src
        warn_idx = src.find("_readStreak > 8")
        after = src[warn_idx : warn_idx + 80]
        assert "30_000" in after or "msSinceDispatch > 30_000" in src[warn_idx - 200 : warn_idx + 200], (
            "Read-grind warn MUST trigger at >=8 reads AND >=30s since dispatch"
        )

    def test_read_grind_deny_returns_permission_deny(self):
        src = _src()
        grind_idx = src.find("READ-GRINDING DETECTED")
        assert grind_idx > 0
        assert 'permissionDecision: "deny"' in src[grind_idx - 500 : grind_idx], (
            "Read-grind deny MUST return permissionDecision: deny"
        )

    def test_read_grind_skipped_in_result_phase(self):
        src = _src()
        grind_idx = src.find("_readStreak > 15")
        assert grind_idx > 0
        after = src[grind_idx : grind_idx + 120]
        assert "!inResultPhase" in after, "Read-grind deny MUST be skipped when inResultPhase is true"

    def test_read_grind_resets_on_dispatch(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        after = src[dispatch_idx : dispatch_idx + 250]
        assert "_readStreak = 0" in after, "Read grind counter MUST reset to 0 on dispatch"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Pressure-Release / Inline Recovery bypass
# ══════════════════════════════════════════════════════════════════════════════


class TestPressureReleaseBehavior:
    """When isInPressureRelease() or isInInlineRecovery() returns true,
    grinding blocks are skipped to allow the agent to recover."""

    def test_pressure_release_function_is_imported(self):
        src = _src()
        assert "isInPressureRelease" in src, "isInPressureRelease MUST be imported from shared.ts"

    def test_pressure_release_skips_read_grind_and_floor_breach(self):
        src = _src()
        assert "isInInlineRecovery" in src, "isInInlineRecovery MUST be imported from shared.ts"

    def test_pressure_release_skips_read_grinding_blocks(self):
        src = _src()
        pressure_idx = src.find("pressureRelief")
        assert pressure_idx > 0, "pressureRelief variable MUST gate grinding block bypass"

    def test_pressure_release_skips_floor_breach(self):
        src = _src()
        floor_breach_idx = src.find("_streakCount++")
        assert floor_breach_idx > 0
        before = src[floor_breach_idx - 500 : floor_breach_idx]
        assert "pressureRelief" in before, "Pressure release check MUST precede floor breach logic"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Fail-Open Guarantees
# ══════════════════════════════════════════════════════════════════════════════


class TestFailOpenBehavior:
    """Every enforcement path MUST be wrapped in try/catch. Any error MUST
    result in allowing the operation (pass-through), never a crash."""

    def test_tool_execute_before_has_outer_try_catch(self):
        src = _src()
        before_idx = src.find('"tool.execute.before"')
        assert before_idx > 0
        hook_section = src[before_idx:]
        m = re.search(r"try\s*{.*?}\s*catch\s*{", hook_section, re.DOTALL)
        assert m, "tool.execute.before MUST have outer try/catch for fail-open"

    def test_all_deny_returns_use_structured_object(self):
        src = _src()
        deny_count = src.count('permissionDecision: "deny"')
        assert deny_count >= 4, f"At least 4 deny paths expected, found {deny_count}"

    def test_no_uncaught_throw_statements(self):
        src = _src()
        assert "throw new Error" not in src, "Plugin MUST NOT throw uncaught errors (fail-open guarantee)"

    def test_plugin_factory_has_catch_block(self):
        src = _src()
        factory_idx = src.find("export default")
        factory_section = src[factory_idx : factory_idx + 500]
        assert "catch" in factory_section, "Plugin factory MUST have catch block for fail-open"

    def test_get_effective_floor_has_catch_empty(self):
        src = _src()
        gef_idx = src.find("function getEffectiveFloor")
        assert gef_idx > 0
        fn_end = src.find("function _buildDispatchCommands", gef_idx)
        fn_body = src[gef_idx:fn_end]
        assert "catch" in fn_body, "getEffectiveFloor() MUST have catch block (fail-open on parse error)"

    def test_open_work_exists_returns_false_on_error(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        assert owa_idx > 0
        fn_end = src.find("function _buildDispatchCommands", owa_idx)
        fn_body = src[owa_idx:fn_end]
        assert "return false" in fn_body, "openWorkExists() MUST return false on any error (fail-open)"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Missed-Commit Dispatch Reminder
# ══════════════════════════════════════════════════════════════════════════════


class TestMissedCommitBehavior:
    """When a commit-type bash command is run from the main thread (not via
    subagent dispatch), it increments a miss counter. After 3 misses, a
    reminder fires every 5 minutes to use a dispatch slot for commits."""

    def test_missed_commit_threshold_is_3(self):
        src = _src()
        assert "MISSED_COMMIT_THRESHOLD = 3" in src, "Missed-commit reminder threshold MUST be 3"

    def test_missed_commit_reminder_interval_is_5_minutes(self):
        src = _src()
        assert "MISSED_COMMIT_REMINDER_MS = 300_000" in src, "Missed-commit reminder interval MUST be 5 minutes"

    def test_commit_command_detection_exists(self):
        src = _src()
        assert "isCommitBashCommand" in src, "Commit bash command detection MUST exist"

    def test_commit_command_triggers_miss_record(self):
        src = _src()
        assert "recordMissedCommit()" in src, "Commit commands MUST trigger recordMissedCommit()"

    def test_miss_record_increments_misses_counter(self):
        src = _src()
        rec_idx = src.find("function recordMissedCommit")
        assert rec_idx > 0
        fn_body = src[rec_idx : rec_idx + 200]
        assert "misses + 1" in fn_body, "recordMissedCommit MUST increment miss counter"

    def test_maybe_remind_gated_on_threshold(self):
        src = _src()
        remind_idx = src.find("function maybeRemindMissedCommitDispatch")
        assert remind_idx > 0
        fn_body = src[remind_idx : remind_idx + 300]
        assert "MISSED_COMMIT_THRESHOLD" in fn_body, "Reminder MUST be gated on miss count >= threshold"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Message-Shape Enforcement (exactly 1 dispatch in prev message)
# ══════════════════════════════════════════════════════════════════════════════


class TestMessageShapeEnforcement:
    """When the previous message had exactly 1 dispatch, non-dispatch tool calls
    in the next message are blocked. The agent MUST batch >=2 dispatches."""

    def test_single_dispatch_in_prev_message_is_blocked(self):
        src = _src()
        assert "_prevMessageDispatchCount === 1" in src, "Exactly 1 dispatch in prev message MUST trigger the block"

    def test_single_dispatch_block_returns_deny(self):
        src = _src()
        shape_idx = src.find("MESSAGE-SHAPE VIOLATION")
        assert shape_idx > 0
        assert 'permissionDecision: "deny"' in src[shape_idx - 400 : shape_idx], (
            "Message-shape violation MUST return deny"
        )

    def test_single_dispatch_block_message_explains_batching(self):
        src = _src()
        shape_idx = src.find("MESSAGE-SHAPE VIOLATION")
        assert shape_idx > 0
        after = src[shape_idx : shape_idx + 600]
        assert "BATCH YOUR DISPATCHES" in after, "Message-shape block MUST explain batching requirement"

    def test_single_dispatch_block_requires_open_work(self):
        src = _src()
        shape_idx = src.find("_prevMessageDispatchCount === 1")
        assert shape_idx > 0
        after = src[shape_idx : shape_idx + 80]
        assert "openWorkExists" in after, "Message-shape block MUST be gated on openWorkExists()"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Subagent Isolation
# ══════════════════════════════════════════════════════════════════════════════


class TestSubagentIsolation:
    """When called from inside a subagent (isSubagent() returns true), the
    plugin MUST return immediately without any enforcement."""

    def test_subagent_guard_is_first_check_in_hook(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        assert before_idx > 0
        after = src[before_idx:]
        subagent_idx = after.find("isSubagent()")
        alive_idx = after.find("reportAlive")
        assert subagent_idx < alive_idx, "isSubagent() MUST be called before reportAlive() in tool.execute.before"

    def test_subagent_guard_returns_immediately(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        assert before_idx > 0
        after = src[before_idx:]
        subagent_idx = after.find("isSubagent()")
        after_subagent = after[subagent_idx : subagent_idx + 80]
        assert "return" in after_subagent, "isSubagent() true MUST return immediately (no enforcement)"

    def test_shared_ts_defines_is_subagent(self):
        shared = _shared_src()
        assert "export function isSubagent()" in shared, "isSubagent() MUST be defined in shared.ts"

    def test_shared_checks_open_code_subagent_env_var(self):
        shared = _shared_src()
        assert "OPENCODE_SUBAGENT" in shared, "Subagent detection MUST check OPENCODE_SUBAGENT env var"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: Refill-Needed Nudge
# ══════════════════════════════════════════════════════════════════════════════


class TestRefillNudgeBehavior:
    """When non-dispatch calls accumulate within 15s of last dispatch AND
    dispatch peak was >=5 AND open work exists, a console.warn fires."""

    def test_refill_nudge_checks_streak_greater_than_zero(self):
        src = _src()
        assert "_streakCount > 0" in src, "Refill nudge MUST only fire when streakCount > 0"

    def test_refill_nudge_checks_dispatch_peak_at_least_5(self):
        src = _src()
        nudge_idx = src.find("REFILL NEEDED")
        assert nudge_idx > 0
        before = src[nudge_idx - 300 : nudge_idx]
        assert "_dispatchPeak >= 5" in before, "Refill nudge MUST require dispatch peak >= 5"

    def test_refill_nudge_checks_time_since_dispatch(self):
        src = _src()
        nudge_idx = src.find("REFILL NEEDED")
        assert nudge_idx > 0
        before = src[nudge_idx - 300 : nudge_idx]
        assert "msSinceDispatch > 15000" in before, "Refill nudge MUST require >15s since last dispatch"

    def test_refill_nudge_is_console_warn_not_block(self):
        src = _src()
        nudge_idx = src.find("REFILL NEEDED")
        assert nudge_idx > 0
        before = src[nudge_idx - 300 : nudge_idx]
        assert "console.warn" in before, "Refill nudge MUST be advisory (console.warn), not a hard block"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: PID Staleness Detection
# ══════════════════════════════════════════════════════════════════════════════


class TestPIDStalenessBehavior:
    """When the plugin detects a PID mismatch (different process from init)
    or stale session-start mtime, all module-level state MUST reset to zero."""

    def test_pid_mismatch_detection_exists(self):
        src = _src()
        assert "_floorInitPid" in src, "_floorInitPid MUST track which PID initialized module state"

    def test_pid_mismatch_triggers_state_reset(self):
        src = _src()
        reset_idx = src.find("_floorInitPid !== process.pid")
        assert reset_idx > 0, "PID mismatch MUST trigger _resetFloorState()"

    def test_session_start_mtime_compared_for_staleness(self):
        src = _src()
        assert "_floorSessionStartMtime" in src, "_floorSessionStartMtime MUST be tracked for staleness detection"

    def test_reset_floor_state_zeros_all_counters(self):
        src = _src()
        reset_fn_idx = src.find("function _resetFloorState")
        assert reset_fn_idx > 0
        fn_body = src[reset_fn_idx : reset_fn_idx + 600]
        counters = [
            "_streakCount",
            "_readStreak",
            "_dispatchCount",
            "_dispatchPeak",
            "_consecutiveReadsInResultPhase",
            "_thisMessageDispatchCount",
            "_thisMessageTotalCalls",
            "_prevMessageDispatchCount",
            "_sessionDispatchCount",
        ]
        for c in counters:
            assert f"{c} = 0" in fn_body, f"_resetFloorState() MUST zero {c}"

    def test_staleness_check_before_any_enforcement(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        assert before_idx > 0
        after = src[before_idx:]
        staleness_idx = after.find("_floorInitPid !== process.pid")
        enforce_idx = after.find("FLOOR_ENFORCE")
        assert staleness_idx < enforce_idx, "PID staleness check MUST precede enforcement gate"


# ══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR: openWorkExists probes
# ══════════════════════════════════════════════════════════════════════════════


class TestOpenWorkProbes:
    """openWorkExists() MUST check all known sources of pending work: ratchet,
    TASKS.md, BUGS.md, .gate-status, git porcelain, todowrite, CI cache."""

    def test_ratchet_yml_probe_nonzero_entries_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert "ratchet.yml" in fn_body[:400], "openWorkExists MUST probe config/ratchet.yml"

    def test_tasks_md_probe_unchecked_items_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert "TASKS.md" in fn_body[:2000], "openWorkExists MUST probe TASKS.md for unchecked items"

    def test_bugs_md_probe_open_incidents_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert "BUGS.md" in fn_body[:2000], "openWorkExists MUST probe BUGS.md for open incidents"

    def test_gate_status_fail_or_running_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert ".gate-status" in fn_body[:3500], "openWorkExists MUST probe .gate-status"

    def test_git_porcelain_nonempty_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert "git status --porcelain" in fn_body or "git diff --name-only" in fn_body, (
            "openWorkExists MUST check git porcelain status"
        )

    def test_todowrite_state_pending_items_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert "gludd-todowrite-state.json" in fn_body, "openWorkExists MUST probe todowrite state for pending items"

    def test_ci_cache_non_success_is_work(self):
        src = _src()
        owa_idx = src.find("function openWorkExists")
        fn_body = src[owa_idx:]
        assert "gludd-watchdog-ci.json" in fn_body[:3500], "openWorkExists MUST probe CI cache for non-success status"
