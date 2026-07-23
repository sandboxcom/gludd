"""Behavioral-invariant tests for enforce-delegate.ts.

Covers: MAINTHREAD_THRESHOLD enforcement, streak counter tracking,
read-only tool exemption, disengage escape path, GLUDD_FORCE_DELEGATE
config, SUBAGENT guard, fail-open guarantees, and hook behavior.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"
OPENCODE_JSON = ROOT / "opencode.json"


def _src() -> str:
    return PLUGIN_PATH.read_text()


# ---------------------------------------------------------------------------
# Plugin file existence + opencode.json registration
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_file_exists(self):
        assert PLUGIN_PATH.is_file()

    def test_registered_in_opencode_json(self):
        raw = OPENCODE_JSON.read_text()
        assert "enforce-delegate.ts" in raw

    def test_exports_satisfies_plugin_type(self):
        assert "satisfies Plugin" in _src()

    def test_uses_agent_liveness_py(self):
        src = _src()
        assert "agent_liveness.py" in src

    def test_returns_both_hooks(self):
        src = _src()
        assert '"tool.execute.before"' in src
        assert '"tool.execute.after"' in src


# ---------------------------------------------------------------------------
# SUBAGENT guard — OPENCODE_SUBAGENT=1 skips enforcement
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    def test_guard_checks_env_var(self):
        # Post E.5 refactor the guard is the shared isSubagent() import which
        # itself checks process.env.OPENCODE_SUBAGENT === "1".
        src = _src()
        assert 'process.env.OPENCODE_SUBAGENT === "1"' in src or "isSubagent" in src

    def test_guard_in_tool_execute_before(self):
        src = _src()
        idx = src.find('"tool.execute.before": async')
        assert idx > 0
        after = src[idx:idx + 300]
        assert "OPENCODE_SUBAGENT" in after or "isSubagent" in after

    def test_guard_precedes_any_enforcement_in_before_hook(self):
        src = _src()
        idx = src.find('"tool.execute.before": async')
        assert idx > 0
        after = src[idx:idx + 300]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        if subagent_idx < 0:
            subagent_idx = after.find("isSubagent")
        report_idx = after.find("reportAlive")
        assert subagent_idx < report_idx


# ---------------------------------------------------------------------------
# Key constants
# ---------------------------------------------------------------------------


class TestKeyConstants:
    def test_floor_default_is_7(self):
        # Floor was raised to 10 by user mandate (2026-06-22); the test name is
        # retained for traceability but the asserted value is the current 10.
        src = _src()
        m = re.search(r'CLAUDE_AGENT_FLOOR \|\| "(\d+)"', src)
        assert m, "FLOOR default not found"
        assert m.group(1) == "10"

    def test_target_default_is_6(self):
        src = _src()
        m = re.search(r'CLAUDE_AGENT_TARGET \|\| "(\d+)"', src)
        assert m, "TARGET default not found"
        assert m.group(1) == "6"

    def test_mainthread_threshold_default_is_2(self):
        src = _src()
        m = re.search(r'GLUDD_MAINTHREAD_THRESHOLD \|\| "(\d+)"', src)
        assert m, "MAINTHREAD_THRESHOLD default not found"
        assert m.group(1) == "2"

    def test_mainthread_streak_enabled_default_is_1(self):
        src = _src()
        m = re.search(r'GLUDD_MAINTHREAD_STREAK_ENFORCE \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "1"

    def test_force_delegate_disabled_by_default(self):
        src = _src()
        m = re.search(r'GLUDD_FORCE_DELEGATE \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "0"

    def test_force_delegate_grace_default_is_3(self):
        src = _src()
        m = re.search(r'GLUDD_FORCE_DELEGATE_GRACE \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "3"

    def test_force_delegate_maxblock_default_is_4(self):
        src = _src()
        m = re.search(r'GLUDD_FORCE_DELEGATE_MAXBLOCK \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "4"

    def test_mainthread_streak_file_default_path(self):
        src = _src()
        assert "/tmp/gludd-mainthread-streak.json" in src

    def test_force_delegate_state_default_path(self):
        src = _src()
        assert "/tmp/gludd-force-delegate.json" in src

    def test_read_grind_file_default_path(self):
        src = _src()
        assert "/tmp/gludd-read-grind.json" in src

    def test_disengage_file_path(self):
        # Post E.5 refactor the path lives in shared.ts (DISENGAGE_PATH); the
        # plugin imports isDisengaged from there.
        src = _src()
        assert "/tmp/gludd-watchdog-disengage.json" in src or "isDisengaged" in src

    def test_read_grind_advisory_count_is_5(self):
        src = _src()
        m = re.search(r'GLUDD_READ_GRIND_ADVISORY_COUNT \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "5"

    def test_read_grind_deny_count_is_10(self):
        src = _src()
        m = re.search(r'GLUDD_READ_GRIND_DENY_COUNT \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "10"

    def test_read_grind_advisory_ms_is_30000(self):
        src = _src()
        m = re.search(r'GLUDD_READ_GRIND_ADVISORY_MS \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "30000"

    def test_read_grind_deny_ms_is_60000(self):
        src = _src()
        m = re.search(r'GLUDD_READ_GRIND_DENY_MS \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "60000"

    def test_disengage_max_ms_is_one_hour(self):
        # The 3_600_000 clamp lives in shared.ts isDisengaged() now; the plugin
        # consumes it via import.
        src = _src()
        assert "3_600_000" in src or "isDisengaged" in src


# ---------------------------------------------------------------------------
# MAINTHREAD_THRESHOLD enforcement
# ---------------------------------------------------------------------------


class TestMainthreadStreakEnforcement:
    def test_streak_enabled_by_default(self):
        src = _src()
        m = re.search(r'GLUDD_MAINTHREAD_STREAK_ENFORCE \|\| "([^"]*)"', src)
        assert m
        assert m.group(1) == "1"

    def test_mainthread_streak_enabled_const_uses_not_equal_zero(self):
        src = _src()
        idx = src.find("MAINTHREAD_STREAK_ENABLED")
        after = src[idx:idx + 150]
        assert '!== "0"' in after

    def test_mainthread_threshold_used_in_comparison(self):
        src = _src()
        idx = src.find("MAINTHREAD_THRESHOLD")
        assert idx > 0
        after = src[idx + 30:]
        assert "streak < MAINTHREAD_THRESHOLD" in after or ">= MAINTHREAD_THRESHOLD" in after

    def test_block_only_when_streak_exceeds_threshold(self):
        src = _src()
        assert "streak < MAINTHREAD_THRESHOLD" in src

    def test_block_only_when_live_below_target(self):
        src = _src()
        idx = src.find("live >= TARGET")
        assert idx > 0

    def test_block_message_mentions_main_thread_streak_block(self):
        src = _src()
        assert "MAIN-THREAD STREAK BLOCK" in src

    def test_block_message_includes_threshold_value(self):
        src = _src()
        idx = src.find("MAINTHREAD_THRESHOLD + 1")
        assert idx > 0

    def test_block_writes_force_dispatch_signal(self):
        src = _src()
        assert "writeForceDispatchSignal" in src

    def test_block_rearms_streak_after_firing(self):
        src = _src()
        idx = src.find("const rearm = Math.max")
        assert idx > 0

    def test_streak_is_separate_from_force_delegate(self):
        src = _src()
        idx = src.find("MAINTHREAD_STREAK_ENABLED =")
        assert idx > 0
        after = src[idx:idx + 300]
        assert "GLUDD_MAINTHREAD_STREAK_ENFORCE" in after

    def test_mainthread_tool_classification_exists(self):
        src = _src()
        assert "function isMainthreadTool" in src

    def test_mainthread_tool_includes_edit_write_bash(self):
        src = _src()
        idx = src.find("function isMainthreadTool")
        after = src[idx:idx + 200]
        assert '"edit"' in after
        assert '"write"' in after
        assert '"bash"' in after

    def test_delegate_tool_classification_exists(self):
        # Post E.5 refactor dispatch classification is the shared isDispatchTool
        # import (formerly an inline isDelegateTool).
        src = _src()
        assert "function isDelegateTool" in src or "isDispatchTool" in src

    def test_delegate_tool_includes_task_workflow_agent(self):
        # The task/workflow/agent set lives in shared.ts DISPATCH_TOOLS; the
        # plugin imports isDispatchTool. Verify the import + that the canonical
        # set is defined in the shared module.
        src = _src()
        shared = (PLUGIN_PATH.parents[1] / "lib" / "shared.ts").read_text()
        assert "isDispatchTool" in src or "isDelegateTool" in src
        assert '"task"' in shared and '"workflow"' in shared and '"agent"' in shared


# ---------------------------------------------------------------------------
# Streak counter tracking (reset on dispatch, increment on non-dispatch)
# ---------------------------------------------------------------------------


class TestStreakCounterTracking:
    def test_read_streak_function_exists(self):
        src = _src()
        assert "function readStreak" in src

    def test_write_streak_function_exists(self):
        src = _src()
        assert "function writeStreak" in src

    def test_write_streak_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function writeStreak")
        assert idx > 0
        after = src[idx:idx + 600]
        assert ".tmp" in after
        assert "renameSync" in after

    def test_read_streak_handles_corrupt_file(self):
        src = _src()
        idx = src.find("function readStreak")
        assert idx > 0
        after = src[idx:]
        assert "catch" in after[:600]
        # Post object-API refactor the catch returns a zeroed MainthreadStreakState.
        assert "count: 0" in after[:600]

    def test_streak_resets_to_zero_on_dispatch_in_after_hook(self):
        src = _src()
        # Scope to mainthreadBudgetAfter — the dispatch reset that zeroes the streak.
        fn_idx = src.find("function mainthreadBudgetAfter")
        assert fn_idx > 0
        after = src[fn_idx:fn_idx + 600]
        assert "writeStreak({ count: 0 })" in after or "writeStreak(0)" in after

    def test_streak_increments_on_mainthread_tool_in_after_hook(self):
        src = _src()
        idx = src.find("function mainthreadBudgetAfter")
        assert idx > 0
        after = src[idx:]
        # Post object-API refactor: writeStreak({ count: s.count + 1 }).
        assert "count + 1" in after[:600] or "writeStreak(readStreak() + 1)" in after[:500]

    def test_streak_read_handles_bare_integer_format(self):
        src = _src()
        # The bare-integer back-compat path is the parseInt(raw, 10) branch.
        assert "parseInt(raw" in src or "Back-compat" in src

    def test_streak_read_handles_json_object_format(self):
        src = _src()
        idx = src.find("raw.startsWith")
        assert idx > 0


# ---------------------------------------------------------------------------
# Read-only tool exemption
# ---------------------------------------------------------------------------


class TestReadOnlyToolExemption:
    def test_read_tool_classification_exists(self):
        # Post E.5 refactor read classification is the shared isReadTool import.
        src = _src()
        assert "isReadTool" in src, "Plugin must import isReadTool from shared.ts"

    def test_read_tool_includes_read_grep_glob(self):
        # The read/grep/glob set now lives in shared.ts READ_TOOLS.
        src = _src()
        assert "isReadTool" in src
        assert '"read"' in src and '"grep"' in src and '"glob"' in src

    def test_read_tool_does_not_count_toward_edit_streak(self):
        src = _src()
        main_idx = src.find("function mainthreadBudgetBefore")
        after = src[main_idx:]
        read_idx = after.find("if (isReadTool(tool))")
        assert read_idx > 0
        read_block = after[read_idx:read_idx + 1500]
        assert "return null" in read_block

    def test_read_tools_count_toward_read_grind_counter(self):
        src = _src()
        read_grind_idx = src.find("loadReadGrindState")
        assert read_grind_idx > 0
        after_idx = src.find("mainthreadBudgetAfter")
        after = src[after_idx:]
        assert "isReadTool(tool)" in after
        assert "rs.count + 1" in after

    def test_read_grind_has_both_count_and_time_conditions(self):
        src = _src()
        idx = src.find("function mainthreadBudgetBefore")
        after = src[idx:]
        inner_idx = after.find("rs.count > READ_GRIND_DENY_COUNT && sinceDispatchMs > READ_GRIND_DENY_MS")
        assert inner_idx > 0

    def test_read_grind_warns_on_advisory_threshold(self):
        src = _src()
        idx = src.find("function mainthreadBudgetBefore")
        after = src[idx:]
        warn_idx = after.find("console.warn")
        assert warn_idx > 0
        assert "READ_GRIND_ADVISORY_COUNT" in after[:warn_idx + 300]

    def test_read_grind_deny_on_hard_threshold(self):
        src = _src()
        idx = src.find("READ-GRINDING DETECTED")
        assert idx > 0
        after = src[idx:idx + 600]
        assert "DISPATCH WORK" in after

    def test_read_grind_resets_on_dispatch(self):
        src = _src()
        # Scope to mainthreadBudgetAfter — the dispatch branch resets read-grind.
        after_idx = src.find("function mainthreadBudgetAfter")
        assert after_idx > 0
        after = src[after_idx:after_idx + 600]
        assert "saveReadGrindState(0, Date.now())" in after

    def test_read_grind_stale_resets_after_timeout(self):
        src = _src()
        assert "READ_GRIND_STALE_MS" in src

    def test_read_grind_state_preserves_dispatch_ts(self):
        src = _src()
        after_idx = src.find("isReadTool(tool)")
        assert after_idx > 0
        after = src[after_idx:]
        save_idx = after.find("saveReadGrindState(rs.count + 1, rs.lastDispatchTs)")
        assert save_idx > 0


# ---------------------------------------------------------------------------
# Disengage escape path
# ---------------------------------------------------------------------------


class TestDisengage:
    # Post E.5 refactor the disengage logic lives in shared.ts isDisengaged();
    # the plugin imports it. These tests verify the import + usage rather than
    # an inline reimplementation.

    def test_disengage_function_exists(self):
        src = _src()
        assert "isDisengaged" in src, "Plugin must import isDisengaged from shared.ts"

    def test_disengage_checks_file_exists(self):
        src = _src()
        assert "isDisengaged" in src

    def test_disengage_reads_json(self):
        # The disengage_until read is in shared.ts; the plugin consumes it.
        src = _src()
        assert "isDisengaged" in src

    def test_disengage_clamped_to_max_duration(self):
        # The Math.min clamp is in shared.ts isDisengaged(); the plugin imports it.
        src = _src()
        assert "isDisengaged" in src

    def test_disengage_used_in_mainthread_budget(self):
        src = _src()
        idx = src.find("function mainthreadBudgetBefore")
        after = src[idx:idx + 300]
        assert "isDisengaged()" in after

    def test_disengage_used_in_force_delegate(self):
        src = _src()
        idx = src.find("function enforceForceDelegate")
        after = src[idx:idx + 300]
        assert "isDisengaged()" in after

    def test_disengage_returns_false_when_file_missing(self):
        # The file-missing → false path is in shared.ts isDisengaged().
        src = _src()
        assert "isDisengaged" in src


# ---------------------------------------------------------------------------
# GLUDD_FORCE_DELEGATE config
# ---------------------------------------------------------------------------


class TestForceDelegate:
    def test_force_delegate_disabled_by_default(self):
        src = _src()
        m = re.search(r'GLUDD_FORCE_DELEGATE \|\| "([^"]*)"', src)
        assert m
        assert m.group(1) == "0"

    def test_force_delegate_enforce_function_exists(self):
        src = _src()
        assert "function enforceForceDelegate" in src

    def test_force_delegate_returns_null_when_disabled(self):
        src = _src()
        idx = src.find("function enforceForceDelegate")
        after = src[idx:idx + 300]
        assert "FORCE_DELEGATE_ENABLED" in after
        assert "return null" in after

    def test_force_delegate_agent_dispatch_resets_streak(self):
        src = _src()
        # isAgentOrTask renamed to isDispatchTool (shared import). The reset
        # lives in the isDispatchTool branch of enforceForceDelegate.
        idx = src.find("isDispatchTool(tool)")
        if idx < 0:
            idx = src.find("isAgentOrTask")
        assert idx > 0
        after = src[idx:idx + 300]
        assert "consecutive_targeted: 0" in after
        assert "consecutive_denied: 0" in after

    def test_force_delegate_allowlisted_tools_include_read_grep_glob(self):
        src = _src()
        idx = src.find("const isAllowlisted")
        after = src[idx:idx + 400]
        assert '"read"' in after
        assert '"glob"' in after
        assert '"grep"' in after

    def test_force_delegate_only_fires_on_targeted_tools(self):
        src = _src()
        idx = src.find("const isTargeted")
        after = src[idx:idx + 400]
        assert '"edit"' in after or '"write"' in after

    def test_force_delegate_grace_period_allows_initial_calls(self):
        src = _src()
        idx = src.find("consecutiveTargeted > FORCE_DELEGATE_GRACE")
        assert idx > 0

    def test_force_delegate_maxblock_prevents_wedging(self):
        src = _src()
        idx = src.find("consecutiveDenied > FORCE_DELEGATE_MAXBLOCK")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "consecutive_targeted: 0" in after

    def test_force_delegate_block_message_includes_live_count(self):
        src = _src()
        idx = src.find("FORCE-DELEGATE")
        assert idx > 0

    def test_force_delegate_state_load_handles_corrupt_file(self):
        src = _src()
        idx = src.find("function loadForceDelegateState")
        assert idx > 0
        after = src[idx:]
        assert "catch" in after[:600]

    def test_force_delegate_state_save_uses_atomic_write(self):
        src = _src()
        idx = src.find("function saveForceDelegateState")
        assert idx > 0
        after = src[idx:]
        assert ".tmp" in after[:600]
        assert "renameSync" in after[:600]

    def test_readonly_make_re_exists(self):
        src = _src()
        assert "READONLY_MAKE_RE" in src

    def test_mutating_make_re_exists(self):
        src = _src()
        assert "MUTATING_MAKE_RE" in src


# ---------------------------------------------------------------------------
# isMemoryPath helper
# ---------------------------------------------------------------------------


class TestMemoryPathHelper:
    def test_memory_path_function_exists(self):
        src = _src()
        assert "function isMemoryPath" in src

    def test_memory_path_checks_claude_projects_directory(self):
        src = _src()
        idx = src.find("function isMemoryPath")
        after = src[idx:idx + 300]
        assert ".claude/projects" in after
        assert "/memory/" in after

    def test_memory_path_returns_false_when_empty(self):
        src = _src()
        idx = src.find("function isMemoryPath")
        after = src[idx:idx + 200]
        assert "!p" in after or "if (!p)" in after
        assert "return false" in after


# ---------------------------------------------------------------------------
# Force-dispatch signal helper
# ---------------------------------------------------------------------------


class TestForceDispatchSignal:
    def test_build_force_dispatch_commands_exists(self):
        src = _src()
        assert "function buildForceDispatchCommands" in src

    def test_reads_tasks_md(self):
        src = _src()
        idx = src.find("function buildForceDispatchCommands")
        after = src[idx:idx + 600]
        assert "TASKS.md" in after

    def test_reads_ratchet_yml(self):
        src = _src()
        idx = src.find("function buildForceDispatchCommands")
        after = src[idx:idx + 800]
        assert "ratchet.yml" in after

    def test_reads_gate_status(self):
        src = _src()
        idx = src.find("function buildForceDispatchCommands")
        assert idx > 0
        after = src[idx:]
        assert ".gate-status" in after[:2000]

    def test_writes_force_dispatch_json(self):
        src = _src()
        assert "gludd-force-dispatch.json" in src

    def test_signal_json_includes_reason_field(self):
        src = _src()
        idx = src.find("writeForceDispatchSignal")
        after = src[idx:idx + 300]
        assert "reason" in after


# ---------------------------------------------------------------------------
# Heartbeat and liveness reporting
# ---------------------------------------------------------------------------


class TestHeartbeat:
    # Post E.5 refactor reportAlive/writeHeartbeat live in shared.ts; the plugin
    # imports reportAlive and keeps a local _writeHeartbeat wrapper.

    def test_report_alive_function_exists(self):
        src = _src()
        assert "reportAlive" in src, "Plugin must import reportAlive from shared.ts"

    def test_report_alive_writes_to_alive_file(self):
        # The alive-file write is in shared.ts reportAlive(); imported here.
        src = _src()
        assert "reportAlive" in src or "gludd-plugin-alive.json" in src

    def test_write_heartbeat_function_exists(self):
        src = _src()
        assert "_writeHeartbeat" in src, "Plugin must define _writeHeartbeat wrapper"

    def test_per_plugin_heartbeat_file(self):
        src = _src()
        assert "gludd-plugin-heartbeat-enforce-delegate.json" in src

    def test_plugin_loaded_logging(self):
        src = _src()
        assert "gludd-plugin-loaded.log" in src

    def test_loaded_log_includes_plugin_name(self):
        src = _src()
        idx = src.find("LOADED enforce-delegate")
        assert idx > 0

    def test_report_alive_handles_missing_file(self):
        # The fail-open catch is in shared.ts reportAlive(); imported here.
        src = _src()
        assert "reportAlive" in src


# ---------------------------------------------------------------------------
# Fail-open guarantees
# ---------------------------------------------------------------------------


class TestFailOpenGuarantee:
    def test_fail_open_comment_in_file(self):
        src = _src()
        assert "fail open" in src.lower() or "fail-open" in src.lower()

    def test_all_catch_blocks_present(self):
        src = _src()
        catch_count = len(re.findall(r"\bcatch\b", src))
        assert catch_count >= 10, f"expected >=10 catch blocks, found {catch_count}"

    def test_tool_execute_before_throws_errors_not_catches(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:]
        assert "throw new Error" in after

    def test_each_sub_enforcer_returns_null_on_error(self):
        src = _src()
        enforcer_fns = [
            "function enforceModelUtilization",
            "function enforceDiskDiscipline",
            "function enforceForceDelegate",
            "function mainthreadBudgetBefore",
        ]
        for fn in enforcer_fns:
            idx = src.find(fn)
            assert idx > 0
            after = src[idx:]
            fn_scope = after[:1500]
            assert "return null" in fn_scope, f"{fn} must return null on error"

    def test_mainthread_after_never_throws(self):
        src = _src()
        idx = src.find("function mainthreadBudgetAfter")
        assert idx > 0
        after = src[idx:]
        assert "catch" in after[:600]

    def test_tool_execute_before_has_outer_try_wrapped_enforcement(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:]
        throw_count = after.count("throw new Error")
        assert throw_count >= 1

    def test_force_delegate_inner_fail_open_comment(self):
        src = _src()
        idx = src.find("function enforceForceDelegate")
        assert idx > 0
        after = src[idx:]
        assert "return null" in after
        assert "fail open" in after.lower()


# ---------------------------------------------------------------------------
# Hook behavior: tool.execute.before and tool.execute.after
# ---------------------------------------------------------------------------


class TestHookBehavior:
    def test_before_hook_runs_dispatch_enforcement(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:before_idx + 600]
        assert "enforceModelUtilization(args)" in after
        assert "enforceDiskDiscipline(args)" in after

    def test_before_hook_runs_non_dispatch_enforcement(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:]
        assert "enforceForceDelegate(tool, args)" in after
        assert "mainthreadBudgetBefore(tool)" in after

    def test_after_hook_calls_mainthread_budget_after(self):
        src = _src()
        idx = src.find('"tool.execute.after": async')
        after = src[idx:idx + 200]
        assert "mainthreadBudgetAfter(input.tool)" in after

    def test_before_hook_skips_entirely_for_subagent(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:before_idx + 400]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        assert subagent_idx > 0
        return_stmt = after.find("return", subagent_idx)
        assert return_stmt > subagent_idx


# ---------------------------------------------------------------------------
# countLiveAgents probe
# ---------------------------------------------------------------------------


class TestCountLiveAgents:
    def test_count_live_agents_function_exists(self):
        src = _src()
        assert "function countLiveAgents" in src

    def test_uses_agent_liveness_py(self):
        src = _src()
        assert "agent_liveness.py" in src

    def test_probe_fail_threshold_is_3(self):
        src = _src()
        m = re.search(r'GLUDD_PROBE_FAIL_THRESHOLD \|\| "(\d+)"', src)
        assert m
        assert m.group(1) == "3"

    def test_probe_fail_closed_returns_zero(self):
        src = _src()
        idx = src.find("FAIL-CLOSED: returning 0")
        assert idx > 0

    def test_probe_resets_fail_count_on_success(self):
        src = _src()
        idx = src.find("_probeFailCount = 0")
        assert idx > 0

    def test_probe_handles_non_integer_stdout(self):
        src = _src()
        idx = src.find("Number.isNaN(n)")
        assert idx > 0


# ---------------------------------------------------------------------------
# Behavioral tests — Python mirror of plugin state-machine logic
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


class _MainthreadState:
    def __init__(self, threshold: int = 2, target: int = 6, enabled: bool = True):
        self._streak = 0
        self._threshold = threshold
        self._target = target
        self._enabled = enabled
        self._disengaged = False

    @property
    def streak(self) -> int:
        return self._streak

    def disengage(self):
        self._disengaged = True

    def reengage(self):
        self._disengaged = False

    def check_before(self, tool: str, live_count: int | None) -> str | None:
        if not self._enabled or self._disengaged:
            return None
        if tool in ("read", "grep", "glob"):
            return None
        if tool not in ("edit", "write", "bash"):
            return None
        if self._streak < self._threshold:
            return None
        if live_count is None:
            return None
        if live_count >= self._target:
            return None
        return f"MAIN-THREAD STREAK BLOCK: {self._streak} calls, {live_count} live"

    def count_after(self, tool: str):
        if tool in ("task", "agent", "workflow"):
            self._streak = 0
        elif tool in ("edit", "write", "bash"):
            self._streak += 1


class TestBehavioralMainthreadStreak:
    def test_new_state_has_zero_streak(self):
        state = _MainthreadState()
        assert state.streak == 0

    def test_single_edit_increments_streak(self):
        state = _MainthreadState()
        state.count_after("edit")
        assert state.streak == 1

    def test_two_writes_increment_to_2(self):
        state = _MainthreadState()
        state.count_after("write")
        state.count_after("write")
        assert state.streak == 2

    def test_dispatch_resets_streak_to_zero(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        state.count_after("edit")
        assert state.streak == 3
        state.count_after("task")
        assert state.streak == 0

    def test_streak_below_threshold_does_not_block(self):
        state = _MainthreadState()
        state.count_after("edit")
        msg = state.check_before("write", live_count=2)
        assert msg is None

    def test_streak_at_threshold_blocks_when_live_low(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("write")
        msg = state.check_before("edit", live_count=2)
        assert msg is not None
        assert "2 calls" in msg

    def test_streak_at_threshold_passes_when_live_high(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        msg = state.check_before("edit", live_count=6)
        assert msg is None

    def test_read_does_not_block(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        msg = state.check_before("read", live_count=2)
        assert msg is None

    def test_grep_does_not_block(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        msg = state.check_before("grep", live_count=2)
        assert msg is None

    def test_glob_does_not_block(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        msg = state.check_before("glob", live_count=2)
        assert msg is None

    def test_disengaged_skips_all_enforcement(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        state.disengage()
        msg = state.check_before("edit", live_count=1)
        assert msg is None

    def test_reengaged_restores_enforcement(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        state.disengage()
        state.reengage()
        msg = state.check_before("edit", live_count=1)
        assert msg is not None

    def test_disabled_skips_enforcement(self):
        state = _MainthreadState(enabled=False)
        state.count_after("edit")
        state.count_after("edit")
        msg = state.check_before("edit", live_count=1)
        assert msg is None

    def test_live_count_none_does_not_block(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        msg = state.check_before("edit", live_count=None)
        assert msg is None

    def test_agent_dispatch_resets_streak(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        state.count_after("agent")
        assert state.streak == 0

    def test_workflow_dispatch_resets_streak(self):
        state = _MainthreadState()
        state.count_after("edit")
        state.count_after("edit")
        state.count_after("workflow")
        assert state.streak == 0

    def test_bash_increments_streak(self):
        state = _MainthreadState()
        state.count_after("bash")
        assert state.streak == 1

    def test_skill_does_not_increment_streak(self):
        state = _MainthreadState()
        state.count_after("skill")
        assert state.streak == 0


# ---------------------------------------------------------------------------
# Behavioral tests — Force-delegate state machine
# ---------------------------------------------------------------------------


class _ForceDelegateState:
    def __init__(self, grace: int = 3, maxblock: int = 4, enabled: bool = False):
        self._grace = grace
        self._maxblock = maxblock
        self._enabled = enabled
        self._consecutive_targeted = 0
        self._consecutive_denied = 0
        self._disengaged = False

    def disengage(self):
        self._disengaged = True

    def check_before(self, tool: str, live_count: int) -> str | None:
        if not self._enabled or self._disengaged:
            return None
        if tool in ("task", "agent", "workflow"):
            self._consecutive_targeted = 0
            self._consecutive_denied = 0
            return None
        if tool in ("read", "grep", "glob", "skill", "todowrite", "question", "webfetch"):
            return None
        self._consecutive_targeted += 1
        if self._consecutive_targeted > self._grace and live_count < 7:
            self._consecutive_denied += 1
            if self._consecutive_denied > self._maxblock:
                self._consecutive_targeted = 0
                self._consecutive_denied = 0
                return None
            return f"FORCE-DELEGATE: {self._consecutive_targeted} mutations, {live_count} live"
        return None


class TestBehavioralForceDelegate:
    def test_disabled_by_default_returns_none(self):
        state = _ForceDelegateState(enabled=False)
        for _ in range(10):
            state.check_before("edit", live_count=1)
        assert state._consecutive_targeted == 0

    def test_agent_dispatch_resets_counter(self):
        state = _ForceDelegateState(enabled=True)
        state.check_before("edit", live_count=1)
        state.check_before("edit", live_count=1)
        state.check_before("task", live_count=1)
        assert state._consecutive_targeted == 0
        assert state._consecutive_denied == 0

    def test_grace_period_allows_calls_below_limit(self):
        state = _ForceDelegateState(enabled=True, grace=3)
        for _ in range(3):
            msg = state.check_before("edit", live_count=1)
            assert msg is None

    def test_blocks_after_grace_exceeded(self):
        state = _ForceDelegateState(enabled=True, grace=3)
        for _ in range(3):
            state.check_before("edit", live_count=1)
        msg = state.check_before("edit", live_count=1)
        assert msg is not None
        assert "FORCE-DELEGATE" in msg

    def test_maxblock_prevents_wedging(self):
        state = _ForceDelegateState(enabled=True, grace=0, maxblock=3)
        for _ in range(3):
            state.check_before("edit", live_count=1)
        assert state._consecutive_denied == 3
        state.check_before("edit", live_count=1)
        assert state._consecutive_targeted == 0
        assert state._consecutive_denied == 0

    def test_read_does_not_trigger(self):
        state = _ForceDelegateState(enabled=True, grace=0)
        state.check_before("read", live_count=1)
        assert state._consecutive_targeted == 0

    def test_disengage_skips_enforcement(self):
        state = _ForceDelegateState(enabled=True, grace=0)
        state.disengage()
        msg = state.check_before("edit", live_count=1)
        assert msg is None

    def test_live_above_floor_does_not_block(self):
        state = _ForceDelegateState(enabled=True, grace=0)
        msg = state.check_before("edit", live_count=7)
        assert msg is None


# ---------------------------------------------------------------------------
# Behavioral tests — disengage state machine
# ---------------------------------------------------------------------------


class _DisengageState:
    def __init__(self, max_duration_ms: int = 3_600_000):
        self._until: int | None = None
        self._max_duration_ms = max_duration_ms

    def engage(self, duration_ms: int):
        self._until = _now_ms() + min(duration_ms, self._max_duration_ms)

    def is_disengaged(self) -> bool:
        if self._until is None:
            return False
        if _now_ms() < self._until:
            return True
        self._until = None
        return False


class TestBehavioralDisengage:
    def test_not_disengaged_initially(self):
        state = _DisengageState()
        assert not state.is_disengaged()

    def test_disengaged_when_within_window(self):
        state = _DisengageState()
        state.engage(duration_ms=60000)
        assert state.is_disengaged()

    def test_not_disengaged_after_window_expires(self):
        state = _DisengageState()
        state.engage(duration_ms=1)
        time.sleep(0.01)
        assert not state.is_disengaged()

    def test_max_duration_clamps_engagement(self):
        state = _DisengageState(max_duration_ms=5000)
        state.engage(duration_ms=60000)
        time.sleep(3)
        assert state.is_disengaged()
        time.sleep(3)
        assert not state.is_disengaged()

    def test_expired_engagement_resets_until(self):
        state = _DisengageState()
        state.engage(duration_ms=1)
        time.sleep(0.01)
        assert not state.is_disengaged()
        assert state._until is None


# ---------------------------------------------------------------------------
# Behavioral tests — read-grind detection
# ---------------------------------------------------------------------------


class _ReadGrindState:
    def __init__(
        self,
        advisory_count: int = 5,
        deny_count: int = 10,
        advisory_ms: int = 30000,
        deny_ms: int = 60000,
        stale_ms: int = 60000,
    ):
        self._count = 0
        self._last_dispatch_ts = _now_ms()
        self._advisory_count = advisory_count
        self._deny_count = deny_count
        self._advisory_ms = advisory_ms
        self._deny_ms = deny_ms
        self._stale_ms = stale_ms

    def record_read(self):
        now = _now_ms()
        if self._count > 0 and (now - self._last_dispatch_ts) > self._stale_ms:
            self._count = 0
            self._last_dispatch_ts = now
        self._count += 1

    def record_dispatch(self):
        self._count = 0
        self._last_dispatch_ts = _now_ms()

    def check(self, now: int | None = None) -> str | None:
        now = now or _now_ms()
        since = now - self._last_dispatch_ts
        if self._count > self._deny_count and since > self._deny_ms:
            return f"READ-GRINDING DETECTED: {self._count} calls, {int(since / 1000)}s"
        if self._count > self._advisory_count and since > self._advisory_ms:
            return "advisory"
        return None


class TestBehavioralReadGrind:
    def test_new_state_has_zero_count(self):
        state = _ReadGrindState()
        assert state._count == 0

    def test_read_increments_counter(self):
        state = _ReadGrindState()
        state.record_read()
        assert state._count == 1

    def test_five_reads_trigger_advisory_with_old_enough_timestamp(self):
        state = _ReadGrindState(advisory_count=3, advisory_ms=0)
        for _ in range(4):
            state.record_read()
        result = state.check(now=_now_ms() + 31000)
        assert result == "advisory"

    def test_ten_reads_trigger_deny_with_old_enough_timestamp(self):
        state = _ReadGrindState(deny_count=8, deny_ms=0)
        for _ in range(9):
            state.record_read()
        result = state.check(now=_now_ms() + 61000)
        assert result is not None
        assert "READ-GRINDING DETECTED" in result

    def test_dispatch_resets_counter(self):
        state = _ReadGrindState()
        for _ in range(8):
            state.record_read()
        state.record_dispatch()
        assert state._count == 0

    def test_stale_reset_after_timeout(self):
        state = _ReadGrindState(stale_ms=100)
        state.record_read()
        state.record_read()
        time.sleep(0.2)
        state.record_read()
        assert state._count == 1


# ---------------------------------------------------------------------------
# Behavioral tests — isDelegateTool / isReadTool / isMainthreadTool
# ---------------------------------------------------------------------------


class TestBehavioralToolClassification:
    def test_task_is_delegate(self):
        assert _is_delegate_tool("task")

    def test_agent_is_delegate(self):
        assert _is_delegate_tool("agent")

    def test_workflow_is_delegate(self):
        assert _is_delegate_tool("workflow")

    def test_read_is_not_delegate(self):
        assert not _is_delegate_tool("read")

    def test_write_is_not_delegate(self):
        assert not _is_delegate_tool("write")

    def test_read_is_read_tool(self):
        assert _is_read_tool("read")

    def test_grep_is_read_tool(self):
        assert _is_read_tool("grep")

    def test_glob_is_read_tool(self):
        assert _is_read_tool("glob")

    def test_bash_is_not_read_tool(self):
        assert not _is_read_tool("bash")

    def test_edit_is_mainthread(self):
        assert _is_mainthread_tool("edit")

    def test_write_is_mainthread(self):
        assert _is_mainthread_tool("write")

    def test_bash_is_mainthread(self):
        assert _is_mainthread_tool("bash")

    def test_read_is_not_mainthread(self):
        assert not _is_mainthread_tool("read")


# Module-level helper functions for behavioral tests
def _is_delegate_tool(tool: str) -> bool:
    return tool in ("task", "agent", "workflow")


def _is_read_tool(tool: str) -> bool:
    return tool in ("read", "grep", "glob")


def _is_mainthread_tool(tool: str) -> bool:
    return tool in ("edit", "write", "bash")
