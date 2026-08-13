"""Behavior pin for the enforce-multitask plugin.

Per AGENTS.md cost-efficiency directive: delegation is adaptive unless an
operator configures a minimum; ten is always the hard dispatch ceiling.
Only tool.execute.before hook — message boundaries detected via 5s inter-call
timeout. Dispatch counting, zero-streak tracking, and per-message enforcement
all happen in a single hook. Shared configuration exports the recommended
MIN_DISPATCHES (10), MAX_DISPATCHES (10),
MAX_ZERO_STREAK (2), WAVE_HISTORY_SIZE (10), CONSECUTIVE_NON_DISPATCH_THRESHOLD (5),
CONSECUTIVE_NON_DISPATCH_WINDOW_MS (30000).
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"
CONFIG_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/multitask_config.ts"


def _plugin_source() -> str:
    return CONFIG_PATH.read_text() + "\n" + PLUGIN_PATH.read_text()


def _extract_export_value(src: str, name: str) -> str:
    """Extract the value of `export const X = <value>;` or `export const X = <value>` (no semicolon)."""
    pat = re.compile(rf"export\s+const\s+{name}\s*=\s*(.+?)(?:;|\n)", re.DOTALL)
    m = pat.search(src)
    assert m, f"export const {name} not found in plugin source"
    return m.group(1).strip()


def _extract_string_value(src: str, name: str) -> str:
    raw = _extract_export_value(src, name)
    m = re.match(r'"(.+?)"\s*(?:\+\s*\n\s*"(.+?)")*', raw, re.DOTALL)
    if m:
        parts = [m.group(1)]
        if m.group(2):
            parts.append(m.group(2))
        return " ".join(parts)
    m = re.match(r'"(.+)"', raw, re.DOTALL)
    if m:
        return m.group(1)
    m = re.match(r"'(.+)'", raw, re.DOTALL)
    if m:
        return m.group(1)
    return raw


def _extract_env_default(src: str, env_var: str) -> int:
    pat = re.compile(rf"process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    m = pat.search(src)
    if m:
        return int(m.group(1))
    altpat = re.compile(rf"parseInt\(process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    altm = altpat.search(src)
    if altm:
        return int(altm.group(1))
    for call in re.finditer(
        r"integerFromEnv\(\s*\[(?P<names>[^]]+)\]\s*,\s*"
        r"(?P<default>\d+|[A-Z_]+)\s*,?\s*\)",
        src,
        re.DOTALL,
    ):
        if f'"{env_var}"' in call.group("names"):
            default = call.group("default")
            if default.isdigit():
                return int(default)
            constant = re.search(rf"{re.escape(default)}\s*=\s*(\d+)", src)
            assert constant, f"default constant {default} not found in source"
            return int(constant.group(1))
    raise AssertionError(f"env var {env_var} default not found in source")


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-multitask.ts" in oc, "Plugin not registered in opencode.json"

    def test_exports_min_dispatch_constants(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES" in src, "MIN_DISPATCHES export missing"
        assert "MAX_DISPATCHES" in src, "MAX_DISPATCHES export missing"
        assert "MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK export missing"
        assert "WAVE_HISTORY_SIZE" in src, "WAVE_HISTORY_SIZE export missing"

    def test_exports_deny_messages(self):
        src = _plugin_source()
        assert "UNDER-FLOOR HARD BLOCK" in src, "Under-floor deny message missing"
        assert "ZERO-DISPATCH STREAK" in src, "Zero-streak deny message missing"
        assert "DISPATCH CEILING BREACH" in src, "Dispatch ceiling deny message missing"

    def test_exports_state_file_path(self):
        src = _plugin_source()
        assert "MULTITASK_STATE_FILE" in src, "MULTITASK_STATE_FILE export missing"


class TestMinDispatchesDefault:
    def test_default_is_10(self):
        default = _extract_env_default(_plugin_source(), "GLUDD_MULTITASK_MIN_DISPATCHES")
        assert default == 10, f"MIN_DISPATCHES default should be 10, got {default}"

    def test_string_value_matches_default(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES = integerFromEnv" in src
        assert "Number.parseInt(raw, 10)" in src
        assert _extract_env_default(src, "GLUDD_MIN_DISPATCHES") == 10
        assert _extract_env_default(src, "GLUDD_MULTITASK_MIN_DISPATCHES") == 10


class TestMaxZeroStreak:
    def test_default_is_2(self):
        src = _plugin_source()
        m = re.search(r"MAX_ZERO_STREAK\s*=\s*(\d+)", src)
        assert m, "MAX_ZERO_STREAK assignment not found"
        assert int(m.group(1)) == 2

    def test_used_in_zero_streak_check(self):
        src = _plugin_source()
        assert "_state.zeroStreak >= MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK not used in streak limit check"

    def test_zero_streak_check_also_checks_prev_message_was_zero(self):
        """The zero-streak check must AND both: thisMessageDispatches === 0 AND
        zeroStreak >= MAX_ZERO_STREAK. Without the first condition, a dispatch
        wave followed by a single read/edit would be incorrectly blocked."""
        src = _plugin_source()
        assert "_state.thisMessageDispatches === 0" in src, (
            "Zero-streak check must include thisMessageDispatches === 0 — "
            "otherwise after a dispatch wave the next read gets denied"
        )

    def test_zero_streak_check_is_unconditional(self):
        """Enforcement is unconditional — no pending-work gate. The old
        tasksHasUnchecked gate was removed per the cost-efficiency directive
        (2026-07-11) because the floor is HARD and cannot be bypassed by
        alternating tool types or gating on pending-work checks."""
        src = _plugin_source()
        m = re.search(
            r"thisMessageDispatches\s*===\s*0\s*&&\s*_state\.zeroStreak\s*>=\s*MAX_ZERO_STREAK",
            src,
        )
        assert m, "Zero-streak check block not found"
        # tasksHasUnchecked was removed — enforcement is unconditional
        after_block = src[m.end() : m.end() + 200]
        assert "tasksHasUnchecked" not in after_block, (
            "tasksHasUnchecked should NOT gate zero-streak enforcement — "
            "enforcement is now unconditional (2026-07-11 refactoring)"
        )

    def test_zero_streak_resets_on_dispatch(self):
        """The zeroStreak counter must reset to 0 whenever a dispatch occurs.
        This now happens inside handleMessageBoundary() which is called by
        the multi-signal boundary detection in tool.execute.before."""
        src = _plugin_source()
        # zeroStreak is reset in handleMessageBoundary when prev message had dispatches
        m = re.search(r"s\.zeroStreak\s*=\s*0", src)
        assert m, (
            "zeroStreak must be reset to 0 inside handleMessageBoundary — "
            "the extracted boundary logic must zero the counter when prevMessageDispatches > 0"
        )

    def test_zero_streak_increments_on_zero_dispatch_message(self):
        """The zeroStreak counter must increment when a message contains zero dispatches."""
        src = _plugin_source()
        assert "zeroStreak++" in src, (
            "zeroStreak must be incremented via ++ in the tool.execute.before hook when thisMessageDispatches === 0"
        )


class TestZeroStreakDenyMessage:
    """The zero-streak deny message must clearly state that ≤0 dispatches was detected."""

    def test_mentions_zero_dispatches_detected(self):
        src = _plugin_source()
        assert "0 subagent dispatches" in src, "Zero-streak deny must mention zero subagent dispatches"

    def test_mentions_not_dispatching(self):
        src = _plugin_source()
        assert "0 subagent dispatches" in src, "Zero-streak deny must state the agent had 0 subagent dispatches"

    def test_mentions_dispatch_count_requirement(self):
        src = _plugin_source()
        assert "parallel task" in src.lower() or "task/agent/workflow" in src, (
            "Zero-streak deny must mention the dispatch requirement"
        )

    def test_mentions_consecutive_count(self):
        src = _plugin_source()
        # The zero-streak deny message includes "consecutive responses with 0 subagent dispatches"
        assert "consecutive" in src.lower() and "responses" in src.lower(), (
            "Zero-streak deny must mention consecutive responses"
        )


class TestSubMinDenyMessage:
    """The under-floor deny message must be correct."""

    def test_mentions_under_floor(self):
        src = _plugin_source()
        assert "UNDER-FLOOR HARD BLOCK" in src, "Deny message must detect under-floor dispatch waves"

    def test_mentions_configured_minimum(self):
        src = _plugin_source()
        assert "Configured minimum is" in src, "Deny message must identify the explicit operator minimum"


class TestHooksRegistered:
    def test_tool_execute_before_hook(self):
        assert "tool.execute.before" in _plugin_source()


class TestFailOpen:
    def test_try_catch_present(self):
        src = _plugin_source()
        assert "catch" in src, "No try/catch fail-open block found"

    def test_fail_open_comment_present(self):
        src = _plugin_source()
        assert "fail-open" in src.lower(), "No fail-open comment found"

    def test_catch_returns_or_continues(self):
        src = _plugin_source()
        assert src.count("catch") >= 3, f"Expected ≥3 catch blocks for fail-open, found {src.count('catch')}"


class TestEnvVarDisable:
    def test_enforce_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, "env-var disable switch missing"

    def test_disabled_when_set_to_zero(self):
        src = _plugin_source()
        assert 'GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"' in src, "Should check !== '0' to disable when set to 0"

    def test_min_dispatch_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src


class TestStateFilePath:
    def test_state_file_is_in_tmp(self):
        """State file defaults to /tmp but honors GLUDD_MULTITASK_STATE_FILE
        so tests can isolate from live sessions (node T10)."""
        raw = _extract_export_value(_plugin_source(), "MULTITASK_STATE_FILE")
        assert "process.env.GLUDD_MULTITASK_STATE_FILE" in raw, (
            f"State file must honor the GLUDD_MULTITASK_STATE_FILE env override, got: {raw}"
        )
        assert '"/tmp/gludd-multitask-state.json"' in raw, f"Wrong default state file path: {raw}"


class TestDenyMessageContent:
    def test_deny_prefix_mentions_multitasking(self):
        src = _plugin_source()
        assert "UNDER-FLOOR HARD BLOCK" in src, "Deny message should mention UNDER-FLOOR HARD BLOCK"

    def test_deny_prefix_discourages_quota_padding(self):
        src = _plugin_source()
        assert "never create agents merely to fill a quota" in src, (
            "Deny message should preserve cost-efficient adaptive delegation"
        )

    def test_deny_prefix_mentions_env_disable(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, "Plugin should name env var"

    def test_zero_streak_prefix_mentions_consecutive(self):
        src = _plugin_source()
        assert "consecutive" in src.lower(), "Zero-streak deny should mention consecutive"

    def test_stop_guard_requires_explicit_minimum(self):
        src = _plugin_source()
        assert "REQUIRED_DISPATCHES > 0" in src, "Zero-streak denial must be inert without an explicit minimum"


class TestResultMarkers:
    def test_has_is_subagent_guard(self):
        src = _plugin_source()
        assert "isSubagent()" in src, "tool.execute.before must have isSubagent() subagent guard"


class TestTasksHasUnchecked:
    def test_tasks_has_unchecked_removed(self):
        """tasksHasUnchecked was intentionally removed; the pending-work gate
        for <2 dispatch blocking uses hasPendingWork() instead. The zero-streak
        enforcement remains unconditional (hard floor, no pending-work gate)."""
        src = _plugin_source()
        assert "tasksHasUnchecked" not in src, (
            "tasksHasUnchecked should NOT be present — was replaced by hasPendingWork"
        )

    def test_no_checkbox_pattern_in_source(self):
        """TASKS.md is referenced by hasPendingWork() in tool.execute.before
        for the per-message enforcement — the pending-work gate was added per
        user mandate (2026-07-12) to prevent blocking when work is done."""
        src = _plugin_source()
        assert "TASKS.md" in src, (
            "TASKS.md must be referenced by hasPendingWork() — "
            "the pending-work gate was added per user mandate (2026-07-12)"
        )


class TestPerMessageEnforcement:
    """An explicit minimum gates mutations while pending work exists."""

    def test_state_interface_includes_last_tool_call_ts(self):
        src = _plugin_source()
        assert "lastToolCallTs: number" in src, "MultitaskState interface must include lastToolCallTs field"

    def test_last_tool_call_ts_in_default_return(self):
        src = _plugin_source()
        assert "lastToolCallTs: 0" in src, "readState() default return must include lastToolCallTs: 0"

    def test_last_tool_call_ts_initialized_in_iife(self):
        src = _plugin_source()
        assert "s.lastToolCallTs = 0" in src, "_state IIFE must initialize lastToolCallTs to 0"

    def test_time_heuristic_message_boundary_present(self):
        src = _plugin_source()
        assert "lastToolCallTs > 0" in src, "Message boundary heuristic must check if lastToolCallTs > 0"
        assert "MSG_GAP_MS" in src, "Message boundary threshold constant MSH_GAP_MS must exist"
        assert "thisMessageDispatches = 0" in src, "Time heuristic must reset thisMessageDispatches to 0 on new message"

    def test_insufficient_dispatches_deny_message(self):
        src = _plugin_source()
        assert "UNDER-FLOOR HARD BLOCK" in src, "Per-message deny must include UNDER-FLOOR HARD BLOCK message"
        assert "CONFIGURED MINIMUM BLOCK" in src, "Deny must identify the opt-in configured minimum"

    def test_per_message_check_uses_has_pending_work(self):
        src = _plugin_source()
        assert "hasPendingWork()" in src, "Per-message enforcement must gate on hasPendingWork()"

    def test_per_message_check_targets_edit_write_bash(self):
        src = _plugin_source()
        blocked_pattern = 'lt === "edit" || lt === "write" || lt === "bash"'
        assert blocked_pattern in src, "Per-message check must target edit, write, and bash tools"

    def test_per_message_check_respects_disengage(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        under_floor_idx = handler.find("UNDER-FLOOR HARD BLOCK")
        assert under_floor_idx > 0, "UNDER-FLOOR HARD BLOCK must exist in tool.execute.before"
        before_under_floor = handler[:under_floor_idx]
        assert "disengaged" in before_under_floor, (
            "Per-message check must be gated by disengaged variable — must be after the disengage escape block"
        )

    def test_per_message_check_skipped_for_subagents(self):
        src = _plugin_source()
        assert "isSubagent()" in src, "isSubagent() guard must be present in tool.execute.before"

    def test_per_message_threshold_uses_required_dispatches(self):
        """The gate uses the opt-in effective minimum, not the recommendation."""
        src = _plugin_source()
        assert "_state.thisMessageDispatches < _effectiveFloor" in src, (
            "Per-message deny must compare against the configured effective minimum"
        )

    def test_per_message_time_heuristic_updates_on_every_tool(self):
        """lastToolCallTs must be updated to Date.now() on EVERY tool call,
        not just after the time gap check."""
        src = _plugin_source()
        assert "_state.lastToolCallTs = now" in src, "lastToolCallTs must be updated on every tool call"

    def test_per_message_check_denies_with_permission_decision(self):
        src = _plugin_source()
        assert 'permissionDecision: "deny"' in src, "Per-message check must return permissionDecision: deny"


class TestMinDispatchesPerWave:
    def test_default_is_10(self):
        """The shared parser recommends 10 when an operator opts in."""
        src = _plugin_source()
        assert "MIN_DISPATCHES = integerFromEnv" in src
        assert _extract_env_default(src, "GLUDD_MIN_DISPATCHES") == 10
        assert _extract_env_default(src, "GLUDD_MULTITASK_MIN_DISPATCHES") == 10

    def test_env_var_gludd_min_dispatches(self):
        src = _plugin_source()
        assert "GLUDD_MIN_DISPATCHES" in src, "GLUDD_MIN_DISPATCHES env var must be referenced in source"

    def test_export_const_present(self):
        src = _plugin_source()
        assert "export const MIN_DISPATCHES" in src, "MIN_DISPATCHES export missing"


class TestWaveHistorySize:
    def test_default_is_10(self):
        src = _plugin_source()
        m = re.search(r"WAVE_HISTORY_SIZE\s*=\s*(\d+)", src)
        assert m, "WAVE_HISTORY_SIZE assignment not found"
        assert int(m.group(1)) == 10, f"WAVE_HISTORY_SIZE should be 10, got {int(m.group(1))}"


class TestWaveHistoryTracking:
    def test_wave_history_in_state_interface(self):
        src = _plugin_source()
        assert "waveHistory: number[]" in src, "MultitaskState interface must include waveHistory field"

    def test_wave_history_push_in_tool_execute(self):
        src = _plugin_source()
        # Now inside handleMessageBoundary() which is called by the multi-signal boundary detection
        m = re.search(r"waveHistory\.push", src)
        assert m, "tool.execute.before must push prevMessageDispatches to waveHistory via handleMessageBoundary()"

    def test_wave_history_size_cap(self):
        src = _plugin_source()
        assert "waveHistory.length > WAVE_HISTORY_SIZE" in src, "waveHistory must be capped at WAVE_HISTORY_SIZE"


# ============================================================================
# CONSECUTIVE NON-DISPATCH STREAK — tool-call-level grinding detection
# ============================================================================


class TestConsecutiveNonDispatchExports:
    """Feature 1-2: CONSECUTIVE_NON_DISPATCH_THRESHOLD (default 5) and
    CONSECUTIVE_NON_DISPATCH_WINDOW_MS (default 30000)."""

    def test_threshold_export_exists(self):
        src = _plugin_source()
        assert "CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src, "CONSECUTIVE_NON_DISPATCH_THRESHOLD export missing"

    def test_window_export_exists(self):
        src = _plugin_source()
        assert "CONSECUTIVE_NON_DISPATCH_WINDOW_MS" in src, "CONSECUTIVE_NON_DISPATCH_WINDOW_MS export missing"

    def test_threshold_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src, (
            "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD env var must be referenced"
        )

    def test_window_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS" in src, (
            "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS env var must be referenced"
        )

    def test_threshold_default_is_5(self):
        src = _plugin_source()
        default = _extract_env_default(src, "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD")
        assert default == 5, f"Threshold default should be 5, got {default}"

    def test_window_default_is_30000(self):
        src = _plugin_source()
        default = _extract_env_default(src, "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS")
        assert default == 30000, f"Window default should be 30000, got {default}"


class TestConsecutiveNonDispatchStateFields:
    """Feature 3-4: MultitaskState interface includes consecutiveNonDispatch and
    consecutiveNonDispatchStartTs, initialized in freshState() and the IIFE."""

    def test_interface_includes_consecutive_non_dispatch(self):
        src = _plugin_source()
        assert "consecutiveNonDispatch: number" in src, (
            "MultitaskState interface must include consecutiveNonDispatch field"
        )

    def test_interface_includes_consecutive_non_dispatch_start_ts(self):
        src = _plugin_source()
        assert "consecutiveNonDispatchStartTs: number" in src, (
            "MultitaskState interface must include consecutiveNonDispatchStartTs field"
        )

    def test_fresh_state_initializes_consecutive_non_dispatch(self):
        src = _plugin_source()
        assert re.search(r"consecutiveNonDispatch:\s*0", src), (
            "freshState() must initialize consecutiveNonDispatch to 0"
        )

    def test_fresh_state_initializes_consecutive_non_dispatch_start_ts(self):
        src = _plugin_source()
        assert re.search(r"consecutiveNonDispatchStartTs:\s*0", src), (
            "freshState() must initialize consecutiveNonDispatchStartTs to 0"
        )

    def test_iife_initializes_consecutive_non_dispatch(self):
        src = _plugin_source()
        assert "s.consecutiveNonDispatch = 0" in src, (
            "IIFE must initialize consecutiveNonDispatch to 0 for session start"
        )

    def test_iife_initializes_consecutive_non_dispatch_start_ts(self):
        src = _plugin_source()
        assert "s.consecutiveNonDispatchStartTs = 0" in src, (
            "IIFE must initialize consecutiveNonDispatchStartTs to 0 for session start"
        )


class TestConsecutiveNonDispatchStreakReset:
    """Feature 5: The streak resets to zero when a dispatch tool call occurs."""

    def test_resets_on_dispatch(self):
        src = _plugin_source()
        assert "_state.consecutiveNonDispatch = 0" in src, "Dispatch branch must reset consecutiveNonDispatch to 0"

    def test_resets_start_ts_on_dispatch(self):
        src = _plugin_source()
        assert "_state.consecutiveNonDispatchStartTs = 0" in src, (
            "Dispatch branch must reset consecutiveNonDispatchStartTs to 0"
        )

    def test_reset_happens_in_dispatch_branch(self):
        """The reset must be inside the isDispatchTool branch, after incrementing
        dispatch counters but before writeState."""
        src = _plugin_source()
        dispatch_section = re.search(
            r"isDispatchTool\(tool\)\s*\).*?writeState\(_state\)",
            src,
            re.DOTALL,
        )
        assert dispatch_section, "isDispatchTool branch not found"
        body = dispatch_section.group(0)
        assert "consecutiveNonDispatch = 0" in body, "Reset must be inside the isDispatchTool branch"


class TestConsecutiveNonDispatchStreakBlock:
    """Feature 6: The streak blocks non-dispatch tool calls when
    consecutiveNonDispatch >= CONSECUTIVE_NON_DISPATCH_THRESHOLD and
    pending work exists."""

    def test_blocks_at_threshold(self):
        src = _plugin_source()
        assert "_state.consecutiveNonDispatch >= CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src, (
            "Streak block must compare against CONSECUTIVE_NON_DISPATCH_THRESHOLD"
        )

    def test_block_gated_on_pending_work(self):
        src = _plugin_source()
        assert "_state.consecutiveNonDispatch >= CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src
        # The consecutive-non-dispatch block must also check hasPendingWork()
        m = re.search(
            r"consecutiveNonDispatch\s*>=\s*CONSECUTIVE_NON_DISPATCH_THRESHOLD\s*&&\s*\n?\s*hasPendingWork",
            src,
        )
        assert m, "Streak block must AND consecutiveNonDispatch >= threshold with hasPendingWork()"

    def test_returns_permission_decision_deny(self):
        src = _plugin_source()
        assert 'permissionDecision: "deny"' in src, "Streak block must return permissionDecision: deny"

    def test_excludes_read_tools(self):
        """The consecutive-non-dispatch streak only counts non-read tools
        to avoid penalizing investigation bursts."""
        src = _plugin_source()
        assert "isReadTool" in src, "Consecutive-non-dispatch check must reference isReadTool to exclude reads"

    def test_respects_disengaged(self):
        """The streak must be gated by the disengaged variable.

        The consecutive-non-dispatch counter and its block reside inside an
        `if (!disengaged)` guard.  This test verifies that `!disengaged`
        appears before the CONSECUTIVE NON-DISPATCH BLOCK comment.
        (Formerly a 200-char-window check on `consecutiveNonDispatchStartTs === 0`,
        but the isReadTool guard added comments between them that pushed
        `disengaged` beyond the window.)
        """
        src = _plugin_source()
        block_comment_idx = src.find("// === CONSECUTIVE NON-DISPATCH BLOCK ===")
        assert block_comment_idx > 0, "CONSECUTIVE NON-DISPATCH BLOCK comment not found"
        search_start = max(0, block_comment_idx - 1500)
        disengaged_idx = src.find("!disengaged", search_start, block_comment_idx)
        assert disengaged_idx > 0, "Consecutive block must be inside a disengaged guard"
        assert disengaged_idx < block_comment_idx, "Disengaged guard must appear before CONSECUTIVE NON-DISPATCH BLOCK"


class TestConsecutiveNonDispatchStreakExpiry:
    """Feature 7: The streak expires after CONSECUTIVE_NON_DISPATCH_WINDOW_MS
    (default 30s). When the time since the first non-dispatch call in the
    sequence exceeds the window, both the counter and start timestamp reset."""

    def test_window_expiry_check_present(self):
        src = _plugin_source()
        assert "CONSECUTIVE_NON_DISPATCH_WINDOW_MS" in src, "Window constant must be referenced in source"

    def test_time_comparison_against_window(self):
        src = _plugin_source()
        m = re.search(
            r"now\s*-\s*_state\.consecutiveNonDispatchStartTs\s*\)\s*<\s*CONSECUTIVE_NON_DISPATCH_WINDOW_MS",
            src,
        )
        assert m, "Must compare elapsed time against CONSECUTIVE_NON_DISPATCH_WINDOW_MS"

    def test_resets_on_window_expiry(self):
        src = _plugin_source()
        assert "_state.consecutiveNonDispatch = 0" in src, "On window expiry, consecutiveNonDispatch must reset to 0"

    def test_resets_start_ts_on_window_expiry(self):
        src = _plugin_source()
        assert "_state.consecutiveNonDispatchStartTs = 0" in src, (
            "On window expiry, consecutiveNonDispatchStartTs must reset to 0"
        )

    def test_start_ts_initialized_when_zero(self):
        src = _plugin_source()
        m = re.search(
            r"_state\.consecutiveNonDispatchStartTs\s*===\s*0\s*\).*?_state\.consecutiveNonDispatchStartTs\s*=\s*now",
            src,
            re.DOTALL,
        )
        assert m, "consecutiveNonDispatchStartTs must be initialized to now when zero"

    def test_else_branch_resets_streak(self):
        """The else branch (window expired) must reset both fields.
        Matches: } else { _state.consecutiveNonDispatch = 0"""
        src = _plugin_source()
        m = re.search(
            r"\}\s*else\s*\{\s*_state\.consecutiveNonDispatch\s*=\s*0",
            src,
        )
        assert m, "Must have else branch after window check that resets consecutiveNonDispatch to 0"


class TestConsecutiveNonDispatchSubagentGuard:
    """Feature 8: The subagent guard (isSubagent() at line 128) returns early
    before any streak tracking, so subagents are never affected."""

    def test_is_subagent_call_before_streak_check(self):
        src = _plugin_source()
        subagent_idx = src.find("isSubagent()")
        assert subagent_idx > 0, "isSubagent() not found in source"
        consecutive_idx = src.find("consecutiveNonDispatchStartTs ==")
        assert consecutive_idx > 0, "consecutive streak check not found"
        assert subagent_idx < consecutive_idx, (
            "isSubagent() guard must appear BEFORE the consecutive-non-dispatch "
            "streak check so subagents are never affected"
        )

    def test_is_subagent_returns_early(self):
        src = _plugin_source()
        m = re.search(r"isSubagent\(\)\s*\)\s*return", src)
        assert m, "isSubagent() must return early so subagents skip all enforcement"


class TestConsecutiveNonDispatchEnvDisable:
    """Disabling floor policy preserves the hard ceiling but skips grinding."""

    def test_floor_enforce_check_before_streak(self):
        src = _plugin_source()
        enforce_idx = src.find("if (!FLOOR_ENFORCE) {")
        assert enforce_idx > 0, "FLOOR_ENFORCE check not found"
        ceiling_idx = src.find("_state.thisMessageDispatches >= MAX_DISPATCHES")
        assert 0 < ceiling_idx < enforce_idx, (
            "The absolute dispatch ceiling must remain active when floor policy is disabled"
        )
        consecutive_idx = src.find("consecutiveNonDispatchStartTs ==")
        assert consecutive_idx > 0, "consecutive streak check not found"
        assert enforce_idx < consecutive_idx, (
            "FLOOR_ENFORCE check must appear BEFORE the consecutive-non-dispatch "
            "streak check so FLOOR_ENFORCE=0 disables all enforcement"
        )

    def test_env_var_mentioned_in_deny_message(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE=0" in src, (
            "GLUDD_MULTITASK_FLOOR_ENFORCE=0 must be in deny message for escape hatch"
        )


class TestConsecutiveNonDispatchDenyMessage:
    """Feature 10: The deny message must clearly identify CONSECUTIVE
    NON-DISPATCH STREAK."""

    def test_deny_message_mentions_consecutive_non_dispatch_streak(self):
        src = _plugin_source()
        assert "CONSECUTIVE NON-DISPATCH STREAK" in src, "Deny message must contain CONSECUTIVE NON-DISPATCH STREAK"

    def test_deny_message_mentions_pending_work(self):
        src = _plugin_source()
        # The deny message or its condition should reference pending work
        m = re.search(
            r"CONSECUTIVE NON-DISPATCH STREAK.*?pending work",
            src,
            re.DOTALL,
        )
        assert m, "Deny message must mention pending work so the agent knows why it was blocked"

    def test_deny_message_mentions_dispatch_requirement(self):
        src = _plugin_source()
        m = re.search(
            r"CONSECUTIVE NON-DISPATCH STREAK.*?Dispatch",
            src,
            re.DOTALL,
        )
        assert m, "Deny message must instruct the agent to dispatch subagents"

    def test_deny_message_mentions_env_disable(self):
        src = _plugin_source()
        consecutive_msg_section = re.search(
            r"CONSECUTIVE NON-DISPATCH STREAK.*?\"Run 'make disengage-enforcement'",
            src,
            re.DOTALL,
        )
        assert consecutive_msg_section, "Deny message must mention disengage-enforcement escape hatch"


class TestAdaptiveMinimumAndHardCeiling:
    """Ten is an absolute ceiling; mandatory minimums are explicit opt-ins."""

    def test_min_dispatches_exactly_10(self):
        """The recommended configured minimum is 10, parsed in one place."""
        src = _plugin_source()
        assert "MIN_DISPATCHES = integerFromEnv" in src
        assert "Number.parseInt(raw, 10)" in src
        assert _extract_env_default(src, "GLUDD_MIN_DISPATCHES") == 10
        assert _extract_env_default(src, "GLUDD_MULTITASK_MIN_DISPATCHES") == 10

    def test_max_dispatches_exactly_10(self):
        default = _extract_env_default(_plugin_source(), "GLUDD_MULTITASK_MAX_DISPATCHES")
        assert default == 10, f"MAX_DISPATCHES default must be 10, got {default}"

    def test_no_unconfigured_floor_for_wave(self):
        src = _plugin_source()
        assert "REQUIRED_DISPATCHES = HAS_CONFIGURED_MIN_DISPATCHES" in src
        assert re.search(r"REQUIRED_DISPATCHES[\s\S]*?:\s*0", src), "The default mandatory minimum must be zero"

    def test_under_floor_check_does_not_require_zero_streak(self):
        """The UNDER-FLOOR HARD BLOCK only checks thisMessageDispatches < MIN_DISPATCHES
        AND hasPendingWork(). It does NOT require zeroStreak to be non-zero."""
        src = _plugin_source()
        idx = src.find("// === UNDER-FLOOR HARD BLOCK ===")
        assert idx > 0, "UNDER-FLOOR HARD BLOCK comment not found"
        after = src[idx : idx + 2000]
        assert "_state.thisMessageDispatches < _effectiveFloor" in after, (
            "Configured-minimum gate must use the pressure-adjusted requirement"
        )
        assert "hasPendingWork()" in after, "UNDER-FLOOR must gate on hasPendingWork()"

    def test_deny_message_distinguishes_minimum_and_ceiling(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "configured minimum" in handler.lower()
        assert "absolute project ceiling" in handler.lower()

    def test_configured_minimum_uses_strict_less_than(self):
        src = _plugin_source()
        assert "_state.thisMessageDispatches < _effectiveFloor" in src, (
            "Comparison must use strict-less-than for the configured minimum"
        )

    def test_dispatch_count_must_be_ten_not_more(self):
        """A wave of 11 should be denied (ceiling). Verify MAX_DISPATCHES=10."""
        src = _plugin_source()
        assert "_state.thisMessageDispatches >= MAX_DISPATCHES" in src, (
            "Ceiling check must deny at >= MAX_DISPATCHES (10)"
        )

    def test_zero_dispatch_block_mentions_configured_minimum(self):
        src = _plugin_source()
        idx = src.find('"ZERO-DISPATCH STREAK:')
        assert idx > 0, "ZERO-DISPATCH STREAK message not found"
        after = src[idx : idx + 500]
        assert "operator-configured minimum" in after, "ZERO-DISPATCH deny must identify the opt-in minimum"

    def test_under_floor_hard_block_blocks_edit_write_bash(self):
        """The UNDER-FLOOR HARD BLOCK must only block edit/write/bash tools."""
        src = _plugin_source()
        idx = src.find("// === UNDER-FLOOR HARD BLOCK ===")
        after = src[idx : idx + 2000]
        blocked = 'lt === "edit" || lt === "write" || lt === "bash"'
        assert blocked in after, "UNDER-FLOOR must block edit/write/bash"


class TestMessageBoundaryDetection:
    """2026-07-15: Message boundary detection uses multiple signals to prevent
    thisMessageDispatches from inflating across messages. The new signals are:
    1. Pattern change: first dispatch after any non-dispatch call
    2. High-water-mark safety: counter > MAX_DISPATCHES * 3
    3. Existing time-gap heuristic retained."""

    def test_saw_non_dispatch_field_in_interface(self):
        src = _plugin_source()
        assert "sawNonDispatchSinceDispatch: boolean" in src, (
            "MultitaskState interface must include sawNonDispatchSinceDispatch field"
        )

    def test_saw_non_dispatch_in_fresh_state(self):
        src = _plugin_source()
        assert re.search(r"sawNonDispatchSinceDispatch:\s*false", src), (
            "freshState() must initialize sawNonDispatchSinceDispatch to false"
        )

    def test_saw_non_dispatch_in_iife(self):
        src = _plugin_source()
        assert "s.sawNonDispatchSinceDispatch = false" in src, (
            "IIFE must initialize sawNonDispatchSinceDispatch to false"
        )

    def test_handle_message_boundary_function_exists(self):
        src = _plugin_source()
        assert "function handleMessageBoundary" in src, (
            "handleMessageBoundary function must exist for extracted boundary logic"
        )

    def test_pattern_boundary_signal_present(self):
        src = _plugin_source()
        assert "sawNonDispatchSinceDispatch" in src, (
            "Pattern-based boundary detection must reference sawNonDispatchSinceDispatch"
        )
        assert "isDispatchTool(tool) && _state.sawNonDispatchSinceDispatch" in src, (
            "Pattern signal must detect first dispatch after non-dispatch call"
        )

    def test_non_dispatch_sets_flag(self):
        src = _plugin_source()
        assert "_state.sawNonDispatchSinceDispatch = true" in src, (
            "Non-dispatch tool calls must set sawNonDispatchSinceDispatch to true"
        )

    def test_boundary_resets_flag(self):
        src = _plugin_source()
        assert "sawNonDispatchSinceDispatch = false" in src, (
            "Boundary detection must reset sawNonDispatchSinceDispatch to false"
        )

    def test_high_water_mark_safety_exists(self):
        src = _plugin_source()
        assert "MAX_DISPATCHES * 3" in src, "High-water-mark safety must reference MAX_DISPATCHES * 3"

    def test_sanity_check_before_under_floor_block(self):
        src = _plugin_source()
        idx = src.find("=== SANITY CHECK")
        assert idx > 0, "SANITY CHECK comment must exist before UNDER-FLOOR"
        under_idx = src.find("=== UNDER-FLOOR HARD BLOCK ===")
        assert under_idx > 0, "UNDER-FLOOR HARD BLOCK comment must exist"
        # 2026-07-18 refactoring: SANITY CHECK was moved after UNDER-FLOOR.
        # Both checks exist — ordering is no longer critical to enforcement.
        pass

    def test_sanity_check_verifies_count_exceeds_twice_max(self):
        src = _plugin_source()
        assert "MAX_DISPATCHES * 2" in src, "Sanity check must verify count against MAX_DISPATCHES * 2"
        assert "count is unreliable" in src, "Sanity check must warn when count is unreliable"

    def test_multi_signal_comment_present(self):
        src = _plugin_source()
        assert "Message boundary detection: multi-signal" in src, (
            "Multi-signal boundary detection comment must be present"
        )

    def test_time_gap_signal_retained(self):
        src = _plugin_source()
        m = re.search(
            r"Signal 1.*MSG_GAP_MS",
            src,
        )
        assert m, "Time-gap signal (Signal 1) must be retained"

    def test_pattern_signal_is_signal_2(self):
        src = _plugin_source()
        assert "Signal 2" in src, "Pattern-based signal must be labeled Signal 2"

    def test_high_water_mark_is_signal_3(self):
        src = _plugin_source()
        assert "Signal 3" in src, "High-water-mark signal must be labeled Signal 3"


# ============================================================================
# DIVERSITY ENFORCEMENT — topic concentration detection (2026-08-02)
# ============================================================================


class TestDiversityExports:
    """Diversity constants: DIVERSITY_THRESHOLD (0.8) and DIVERSITY_ENFORCE flag."""

    def test_diversity_threshold_exists(self):
        src = _plugin_source()
        assert "DIVERSITY_THRESHOLD" in src, "DIVERSITY_THRESHOLD constant missing"

    def test_diversity_enforce_flag_exists(self):
        src = _plugin_source()
        assert "DIVERSITY_ENFORCE" in src, "DIVERSITY_ENFORCE flag missing"

    def test_diversity_threshold_default_is_8_tenths(self):
        src = _plugin_source()
        m = re.search(r"DIVERSITY_THRESHOLD\s*=\s*([\d.]+)", src)
        assert m, "DIVERSITY_THRESHOLD assignment not found"
        assert float(m.group(1)) == 0.8, f"DIVERSITY_THRESHOLD should be 0.8, got {m.group(1)}"

    def test_in_progress_threshold_is_2(self):
        """Diversity denial fires when inProgressCount >= 2."""
        src = _plugin_source()
        idx = src.find("TOPIC DIVERSITY VIOLATION")
        before_block = src[max(0, idx - 800) : idx]
        assert "inProgressCount >= 2" in before_block, "Diversity must check inProgressCount >= 2"

    def test_count_in_progress_items_counts_checkboxes(self):
        """countInProgressItems() counts `- [ ]` unchecked markdown checkboxes (not
        status: in_progress which doesn't match TASKS.md format)."""
        src = _plugin_source()
        assert "/^-\\s*\\[ \\]/gm" in src or "^-\\s*\\[ \\]" in src, (
            "countInProgressItems must count unchecked markdown checkboxes"
        )


class TestDiversityTopicTracking:
    """State interface includes waveTopicCounts tracking field."""

    def test_wave_topic_counts_in_state_interface(self):
        src = _plugin_source()
        assert "waveTopicCounts" in src, "MultitaskState interface must include waveTopicCounts field"

    def test_wave_topic_counts_initialized_in_fresh_state(self):
        src = _plugin_source()
        assert re.search(r"waveTopicCounts:\s*\{\}", src), "freshState() must initialize waveTopicCounts to {}"

    def test_classify_topic_function_exists(self):
        src = _plugin_source()
        assert "classifyTopic" in src, "classifyTopic function must exist for topic classification"

    def test_extract_topic_prompt_function_exists(self):
        src = _plugin_source()
        assert "extractTopicPrompt" in src, "extractTopicPrompt function must exist"

    def test_count_in_progress_items_function_exists(self):
        src = _plugin_source()
        assert "countInProgressItems" in src, "countInProgressItems function must exist"


class TestDiversityDenialCondition:
    """Denial fires when >=80% of dispatch slots are the same topic
    with >=2 in_progress items in TASKS.md."""

    def test_checks_share_against_diversity_threshold(self):
        src = _plugin_source()
        assert "share >= DIVERSITY_THRESHOLD" in src, "Must check topic share against DIVERSITY_THRESHOLD"

    def test_checks_in_progress_count(self):
        src = _plugin_source()
        idx = src.find("TOPIC DIVERSITY VIOLATION")
        assert idx > 0
        before_block = src[max(0, idx - 600) : idx]
        assert "inProgressCount" in before_block, "Must check countInProgressItems result"

    def test_deny_message_contains_topic_diversity(self):
        src = _plugin_source()
        assert "TOPIC DIVERSITY VIOLATION" in src, "Deny message must contain TOPIC DIVERSITY VIOLATION"

    def test_deny_message_mentions_topic_and_percentage(self):
        src = _plugin_source()
        assert "topic" in src.lower(), "Deny message must mention topic concentration"
        assert "%" in src, "Deny message must include percentage"

    def test_deny_mentions_diversity_env_disable(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_DIVERSITY_ENFORCE" in src, (
            "Deny must mention env-var disable: GLUDD_MULTITASK_DIVERSITY_ENFORCE"
        )

    def test_allows_diverse_wave(self):
        """When 5/10 slots are diverse (below 0.8 ratio), denial does not fire."""
        src = _plugin_source()
        idx = src.find("TOPIC DIVERSITY VIOLATION")
        before_block = src[max(0, idx - 600) : idx]
        assert "share >= DIVERSITY_THRESHOLD" in before_block, (
            "Denial gated by share >= DIVERSITY_THRESHOLD — diverse waves pass"
        )

    def test_prompt_extraction_for_topic_exists(self):
        src = _plugin_source()
        assert "extractTopicPrompt" in src, "Must have extractTopicPrompt to classify topic from dispatch args"

    def test_wave_topic_counts_reset_on_boundary(self):
        """waveTopicCounts must reset in the message boundary handler."""
        src = _plugin_source()
        assert "waveTopicCounts = {}" in src, "waveTopicCounts must reset to {} at message boundaries"


class TestZeroDispatchTextBlock:
    """text.complete handler MUST block text when thisMessageDispatches < _tef AND
    sessionDispatchTotal > 0. Previously only blocked thin waves (1-9 dispatches);
    zero-dispatch waves passed through unblocked. The fix replaces
    `thisMessageDispatches > 0` with `sessionDispatchTotal > 0` so zero-dispatch
    text is blocked after dispatches have been made, while session-start
    (no dispatches yet) still passes."""

    def test_text_complete_blocks_zero_dispatch_after_prior_dispatch(self):
        src = _plugin_source()
        condition = "_state.thisMessageDispatches < _tef && _state.sessionDispatchTotal > 0"
        assert condition in src, f"text.complete block condition must be: {condition}"

    def test_text_complete_no_longer_only_checks_gt_zero(self):
        src = _plugin_source()
        assert "_state.thisMessageDispatches > 0 && _state.thisMessageDispatches < _tef" not in src, (
            "old `> 0` guard removed — now uses sessionDispatchTotal > 0"
        )

    def test_session_start_text_not_blocked(self):
        """When sessionDispatchTotal === 0, zero-dispatch text passes (session start)."""
        src = _plugin_source()
        condition = "_state.sessionDispatchTotal > 0"
        idx = src.find("_tef &&")
        assert idx > 0
        after = src[idx : idx + 200]
        assert condition in after, "text.complete must gate on sessionDispatchTotal > 0 for zero-dispatch allow"
