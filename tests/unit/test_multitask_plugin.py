"""Behavior pin for the enforce-multitask plugin.

Per AGENTS.md cost-efficiency directive (2026-07-11): floor is 7 agents max.
Every assistant response MUST contain either zero or >=7 dispatches per wave.
<7 dispatches are DENIED when >=2 pending items exist. This test extracts
exported constants from the TypeScript source and validates them against the
spec. Also verifies zero-streak enforcement: after N consecutive zero-dispatch
responses, enforcement fires to FORCE a dispatch (detects ≤0 dispatches).
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


def _extract_export_value(src: str, name: str) -> str:
    """Extract the value of `export const X = <value>;`"""
    pat = re.compile(rf"export\s+const\s+{name}\s*=\s*(.+?);", re.DOTALL)
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
        assert "MAX_ZERO_STREAK" in src, "MAX_ZERO_STREAK export missing"

    def test_exports_deny_messages(self):
        src = _plugin_source()
        assert "MULTITASKING FLOOR BREACH" in src, "Min-dispatch deny message missing"
        assert "ZERO-DISPATCH STREAK" in src, "Zero-streak deny message missing"
        assert "MUST DISPATCH" in src, "text.complete block message missing"
        assert "MESSAGE BLOCKED" in src, "text.complete <7 dispatch block message missing"

    def test_exports_dispatch_tools(self):
        src = _plugin_source()
        assert "DISPATCH_TOOLS" in src, "DISPATCH_TOOLS export missing"

    def test_exports_state_file_path(self):
        src = _plugin_source()
        assert "MULTITASK_STATE_FILE" in src, "MULTITASK_STATE_FILE export missing"


class TestMinDispatchesDefault:
    def test_default_is_7(self):
        default = _extract_env_default(_plugin_source(), "GLUDD_MULTITASK_MIN_DISPATCHES")
        assert default == 7, f"MIN_DISPATCHES default should be 7, got {default}"

    def test_string_value_matches_default(self):
        raw = _extract_export_value(_plugin_source(), "MIN_DISPATCHES")
        assert "7" in raw


class TestMaxZeroStreak:
    def test_default_is_2(self):
        src = _plugin_source()
        m = re.search(r"MAX_ZERO_STREAK\s*=\s*(\d+)", src)
        assert m, "MAX_ZERO_STREAK assignment not found"
        assert int(m.group(1)) == 2

    def test_used_in_zero_streak_check(self):
        src = _plugin_source()
        assert "_state.zeroStreak >= MAX_ZERO_STREAK" in src, (
            "MAX_ZERO_STREAK not used in streak limit check"
        )

    def test_zero_streak_check_also_checks_prev_message_was_zero(self):
        """The zero-streak check must AND both: prevMessageDispatches === 0 AND
        zeroStreak >= MAX_ZERO_STREAK. Without the first condition, a dispatch
        wave followed by a single read/edit would be incorrectly blocked."""
        src = _plugin_source()
        assert "_state.prevMessageDispatches === 0" in src, (
            "Zero-streak check must include prevMessageDispatches === 0 — "
            "otherwise after a dispatch wave the next read gets denied"
        )

    def test_zero_streak_check_is_unconditional(self):
        """Enforcement is unconditional — no pending-work gate. The old
        tasksHasUnchecked gate was removed per the cost-efficiency directive
        (2026-07-11) because the floor is HARD and cannot be bypassed by
        alternating tool types or gating on pending-work checks."""
        src = _plugin_source()
        m = re.search(
            r"prevMessageDispatches\s*===\s*0\s*&&\s*_state\.zeroStreak\s*>=\s*MAX_ZERO_STREAK",
            src,
        )
        assert m, "Zero-streak check block not found"
        # tasksHasUnchecked was removed — enforcement is unconditional
        after_block = src[m.end():m.end()+200]
        assert "tasksHasUnchecked" not in after_block, (
            "tasksHasUnchecked should NOT gate zero-streak enforcement — "
            "enforcement is now unconditional (2026-07-11 refactoring)"
        )

    def test_zero_streak_resets_on_dispatch(self):
        """The zeroStreak counter must reset to 0 whenever a dispatch occurs.
        This happens in text.complete: if thisMessageDispatches !== 0, zeroStreak = 0."""
        src = _plugin_source()
        assert "_state.zeroStreak = 0" in src, (
            "zeroStreak must be reset to 0 on dispatch — "
            "the text.complete hook must zero the counter when thisMessageDispatches > 0"
        )

    def test_zero_streak_increments_on_zero_dispatch_message(self):
        """The zeroStreak counter must increment when a message contains zero dispatches."""
        src = _plugin_source()
        assert "zeroStreak++" in src, (
            "zeroStreak must be incremented via ++ in the text.complete hook "
            "when thisMessageDispatches === 0"
        )


class TestZeroStreakDenyMessage:
    """The zero-streak deny message must clearly state that ≤0 dispatches was detected."""

    def test_mentions_zero_dispatches_detected(self):
        src = _plugin_source()
        assert "0 subagent dispatches" in src, (
            "Zero-streak deny must mention zero subagent dispatches"
        )

    def test_mentions_not_dispatching(self):
        src = _plugin_source()
        assert "0 subagent dispatches" in src, (
            "Zero-streak deny must state the agent had 0 subagent dispatches"
        )

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
    """The sub-minimum deny message (<7 dispatches when floor is 7) must be correct."""

    def test_mentions_sub_minimum(self):
        src = _plugin_source()
        assert "MULTITASKING FLOOR BREACH" in src, (
            "Deny message must detect sub-minimum dispatch waves"
        )

    def test_mentions_required_min(self):
        src = _plugin_source()
        assert "Codified floor" in src, (
            "Deny message must mention the codified floor requirement"
        )


class TestDispatchTools:
    def test_contains_task_agent_workflow(self):
        src = _plugin_source()
        assert '"task"' in src
        assert '"agent"' in src
        assert '"workflow"' in src

    def test_is_frozen(self):
        src = _plugin_source()
        assert "Object.freeze" in src, "DISPATCH_TOOLS not frozen"


class TestHooksRegistered:
    def test_tool_execute_before_hook(self):
        assert "tool.execute.before" in _plugin_source()

    def test_text_complete_hook(self):
        assert "experimental.text.complete" in _plugin_source()

    def test_session_idle_hook(self):
        assert "session.idle" in _plugin_source()


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
        assert 'GLUDD_MULTITASK_FLOOR_ENFORCE !== "0"' in src, (
            "Should check !== '0' to disable when set to 0"
        )

    def test_min_dispatch_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src


class TestStateFilePath:
    def test_state_file_is_in_tmp(self):
        raw = _extract_string_value(_plugin_source(), "MULTITASK_STATE_FILE")
        assert raw == "/tmp/gludd-multitask-state.json", f"Wrong state file path: {raw}"


class TestDenyMessageContent:
    def test_deny_prefix_mentions_multitasking(self):
        src = _plugin_source()
        assert "MULTITASKING" in src, "Deny message should mention MULTITASKING"

    def test_deny_prefix_mentions_batch_wider(self):
        src = _plugin_source()
        assert "parallel task/agent/workflow dispatches" in src, (
            "Deny message should mention parallel dispatch requirement"
        )

    def test_deny_prefix_mentions_env_disable(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, "Plugin should name env var"

    def test_zero_streak_prefix_mentions_consecutive(self):
        src = _plugin_source()
        assert "consecutive" in src.lower(), "Zero-streak deny should mention consecutive"

    def test_stop_guard_prefix_mentions_unconditional(self):
        src = _plugin_source()
        assert "UNCONDITIONAL" in src, (
            "Zero-streak deny should mention UNCONDITIONAL enforcement — "
            "there is no pending-work gate"
        )


class TestResultMarkers:
    def test_has_result_markers(self):
        src = _plugin_source()
        assert "task result" in src
        assert "completed" in src
        assert "subagent result" in src


class TestTextCompleteResearchFinding:
    """text.complete never fires on tool output (2026-07-12 research finding).
    The hook only fires on text-end LLM stream events — _input.role never
    exists in the payload. So no tool-output guard is needed. These tests
    verify the research finding is documented in the plugin source to prevent
    re-addition of dead isToolOutput code."""

    def test_research_finding_comment_present(self):
        src = _plugin_source()
        assert "text.complete hook NEVER fires on tool output" in src, (
            "Plugin must document that text.complete never receives tool output — "
            "the RESEARCH FINDING comment prevents re-adding dead isToolOutput guards"
        )

    def test_isToolOutput_variable_removed(self):
        """isToolOutput variable declaration must be removed. The comment may
        mention it as a warning, but no `const isToolOutput` should exist."""
        src = _plugin_source()
        assert "const isToolOutput" not in src, (
            "const isToolOutput variable declaration must be removed"
        )
        assert "let isToolOutput" not in src, (
            "let isToolOutput variable declaration must be removed"
        )

    def test_if_isToolOutput_block_removed(self):
        """`if (isToolOutput) { return output }` block must be removed."""
        src = _plugin_source()
        assert "if (isToolOutput)" not in src, (
            "Dead if(isToolOutput) return block must be removed"
        )

    def test_research_finding_header_comment_present(self):
        """RESEARCH FINDING comment must document the finding about text.complete
        never firing on tool output."""
        src = _plugin_source()
        assert "RESEARCH FINDING" in src, (
            "RESEARCH FINDING comment must be present in the plugin source"
        )

    def test_role_field_not_referenced_as_guard(self):
        """No role-based guard code (like `_input.role !== 'assistant'`) in
        text.complete handler. The comment may mention _input.role as a warning."""
        src = _plugin_source()
        handler_start = src.find('"experimental.text.complete"')
        after_handler = src[handler_start:]
        assert '!== "assistant"' not in after_handler, (
            "Role comparison guard must not exist in text.complete handler"
        )
        assert '"role" in _input' not in after_handler, (
            "No 'role' in _input guard code should exist in text.complete handler"
        )


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
        """TASKS.md is now referenced by hasPendingWork() in text.complete
        for the <7 dispatch block — the pending-work gate was added per
        user mandate (2026-07-12) to prevent blocking when work is done."""
        src = _plugin_source()
        assert "TASKS.md" in src, (
            "TASKS.md must be referenced by hasPendingWork() — "
            "the pending-work gate was added per user mandate (2026-07-12)"
        )


class TestPerMessageEnforcement:
    """Per-message dispatch-count enforcement added 2026-07-12:
    tool.execute.before blocks Edit/Write/Bash when current message
    has <7 dispatches AND pending work exists."""

    def test_state_interface_includes_last_tool_call_ts(self):
        src = _plugin_source()
        assert "lastToolCallTs: number" in src, (
            "MultitaskState interface must include lastToolCallTs field"
        )

    def test_last_tool_call_ts_in_default_return(self):
        src = _plugin_source()
        assert "lastToolCallTs: 0" in src, (
            "readState() default return must include lastToolCallTs: 0"
        )

    def test_last_tool_call_ts_initialized_in_iife(self):
        src = _plugin_source()
        assert "s.lastToolCallTs = 0" in src, (
            "_state IIFE must initialize lastToolCallTs to 0"
        )

    def test_time_heuristic_message_boundary_present(self):
        src = _plugin_source()
        assert "lastToolCallTs > 0" in src, (
            "Message boundary heuristic must check if lastToolCallTs > 0"
        )
        assert "> 5000" in src, (
            "Message boundary threshold must be 5000ms (5s)"
        )
        assert "thisMessageDispatches = 0" in src, (
            "Time heuristic must reset thisMessageDispatches to 0 on new message"
        )

    def test_insufficient_dispatches_deny_message(self):
        src = _plugin_source()
        assert "INSUFFICIENT DISPATCHES" in src, (
            "Per-message deny must include INSUFFICIENT DISPATCHES message"
        )
        assert "Add dispatches and resend" in src, (
            "Deny must instruct to add dispatches and resend"
        )

    def test_per_message_check_uses_has_pending_work(self):
        src = _plugin_source()
        assert "hasPendingWork()" in src, (
            "Per-message enforcement must gate on hasPendingWork()"
        )

    def test_per_message_check_targets_edit_write_bash(self):
        src = _plugin_source()
        blocked_pattern = 'lt === "edit" || lt === "write" || lt === "bash"'
        assert blocked_pattern in src, (
            "Per-message check must target edit, write, and bash tools"
        )

    def test_per_message_check_respects_disengage(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        insuff_idx = handler.find("INSUFFICIENT DISPATCHES")
        assert insuff_idx > 0, "INSUFFICIENT DISPATCHES must exist in tool.execute.before"
        before_insuff = handler[:insuff_idx]
        assert "disengaged" in before_insuff, (
            "Per-message check must be gated by disengaged variable — "
            "must be after the disengage escape block"
        )

    def test_per_message_check_skipped_for_subagents(self):
        src = _plugin_source()
        assert 'OPENCODE_SUBAGENT === "1"' in src, (
            "OPENCODE_SUBAGENT guard must be present in tool.execute.before"
        )

    def test_per_message_threshold_is_7(self):
        """The per-message enforcement must block when dispatch count is <7
        (i.e., 0-6 dispatches in the current message)."""
        src = _plugin_source()
        assert "_state.thisMessageDispatches < 7" in src, (
            "Per-message threshold must be <7 dispatches"
        )

    def test_per_message_time_heuristic_updates_on_every_tool(self):
        """lastToolCallTs must be updated to Date.now() on EVERY tool call,
        not just after the time gap check."""
        src = _plugin_source()
        assert "_state.lastToolCallTs = now" in src, (
            "lastToolCallTs must be updated on every tool call"
        )

    def test_per_message_check_denies_with_permission_decision(self):
        src = _plugin_source()
        assert 'permissionDecision: "deny"' in src, (
            "Per-message check must return permissionDecision: deny"
        )
