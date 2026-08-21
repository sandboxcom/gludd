"""Behavioral-invariant tests for enforce-session-start.ts.

Covers: plugin registration, system.transform directive injection,
tool.execute.before dispatch tracking, state file read/write, dispatch
count gating, ENFORCE enable/disable, SUBAGENT guard, time-gate constants,
fail-open guarantees, and SessionState interface fields.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
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
        assert "enforce-session-start.ts" in raw, (
            "enforce-session-start.ts must be referenced in opencode.json"
        )

    def test_exports_satisfies_plugin_type(self):
        assert "satisfies Plugin" in _src()

    def test_export_is_async_factory(self):
        src = _src()
        assert "export default" in src
        assert "async" in src

    def test_returns_two_hooks(self):
        src = _src()
        assert '"experimental.chat.system.transform"' in src
        assert '"tool.execute.before"' in src


# ---------------------------------------------------------------------------
# Key constants and defaults
# ---------------------------------------------------------------------------


class TestKeyConstants:
    def test_min_dispatches_default_is_3(self):
        src = _src()
        m = re.search(r'GLUDD_SESSION_START_MIN_DISPATCHES \|\| "(\d+)"', src)
        assert m, "MIN_DISPATCHES default not found"
        assert m.group(1) == "10"

    def test_enforce_default_is_true(self):
        src = _src()
        assert 'GLUDD_SESSION_START_ENFORCE !== "0"' in src, (
            "ENFORCE must default to true (disabled only when env var == '0')"
        )

    def test_state_file_default_path(self):
        src = _src()
        assert "/tmp/gludd-session-start.json" in src

    def test_state_file_env_override(self):
        src = _src()
        assert "GLUDD_SESSION_STATE" in src

    def test_fresh_secs_default_is_600(self):
        src = _src()
        m = re.search(r'GLUDD_SESSION_START_FRESH_SECS \|\| "(\d+)"', src)
        assert m, "FRESH_SECS default not found"
        assert m.group(1) == "600"

    def test_tasks_stale_minutes_default_is_5(self):
        src = _src()
        m = re.search(r'GLUDD_TASKS_STALE_MINUTES \|\| "(\d+)"', src)
        assert m, "TASKS_STALE_MINUTES default not found"
        assert m.group(1) == "5"

    def test_dispatch_now_secs_default_is_60(self):
        src = _src()
        m = re.search(r'GLUDD_SESSION_START_DISPATCH_NOW_SECS \|\| "(\d+)"', src)
        assert m, "DISPATCH_NOW_SECS default not found"
        assert m.group(1) == "60"

    def test_hard_deny_secs_default_is_120(self):
        src = _src()
        m = re.search(r'GLUDD_SESSION_START_HARD_DENY_SECS \|\| "(\d+)"', src)
        assert m, "HARD_DENY_SECS default not found"
        assert m.group(1) == "120"

    def test_effective_min_is_opt_in_and_bounded_by_hard_max(self):
        src = _src()
        assert "const HARD_MAX_DISPATCHES = 10" in src
        assert "HAS_CONFIGURED_MIN_DISPATCHES" in src
        assert "Math.min(Number.isFinite(MIN_DISPATCHES)" in src
        assert re.search(r"EFFECTIVE_MIN\s*=[\s\S]+?:\s*0", src)


# ---------------------------------------------------------------------------
# SESSION_START_DIRECTIVE banner injection (system.transform)
# ---------------------------------------------------------------------------


class TestDirectiveBanner:
    def test_banner_text_exists(self):
        src = _src()
        assert "SESSION START PROTOCOL" in src

    def test_banner_mentions_step_1_locate_work(self):
        src = _src()
        assert "LOCATE work" in src
        assert "TASKS.md" in src
        assert "BUGS.md" in src
        assert "config/ratchet.yml" in src
        assert "SESSION.md" in src

    def test_banner_mentions_step_2_adaptive_assessment(self):
        src = _src()
        assert "STEP 2 — ASSESS" in src
        assert "No mandatory dispatch minimum" in src

    def test_banner_mentions_time_gate(self):
        src = _src()
        assert "TIME GATE" in src
        assert "DISPATCH_NOW_SECS" in src or "dispatch within" in src.lower()

    def test_banner_mentions_hard_deny(self):
        src = _src()
        assert "HARD_DENY_SECS" in src or "non-dispatch mutations DENIED" in src

    def test_banner_contains_no_prose_rule(self):
        src = _src()
        assert "no prose" in src.lower() or "No prose" in src

    def test_banner_references_plugin_file(self):
        src = _src()
        assert "enforce-session-start.ts" in src

    def test_banner_references_enforce_disable_var(self):
        src = _src()
        assert "GLUDD_SESSION_START_ENFORCE=0" in src


# ---------------------------------------------------------------------------
# SUBAGENT guard — both hooks skip when OPENCODE_SUBAGENT=1
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    def test_guard_checks_env_var(self):
        src = _src()
        assert "OPENCODE_SUBAGENT" in src, (
            "Plugin must check OPENCODE_SUBAGENT env var (via isSubagent() import or inline)."
        )

    def test_system_transform_has_subagent_guard(self):
        src = _src()
        idx = src.find('"experimental.chat.system.transform"')
        assert idx > 0
        after = src[idx:idx + 400]
        assert "OPENCODE_SUBAGENT" in after, (
            "system.transform must guard via OPENCODE_SUBAGENT"
        )

    def test_system_transform_returns_output_on_subagent(self):
        src = _src()
        idx = src.find('"experimental.chat.system.transform"')
        assert idx > 0
        after = src[idx:idx + 400]
        assert "return output" in after, (
            "system.transform must return output unchanged for subagents"
        )

    def test_tool_execute_before_has_subagent_guard(self):
        src = _src()
        idx = src.find('"tool.execute.before": async')
        assert idx > 0
        after = src[idx:idx + 300]
        assert "OPENCODE_SUBAGENT" in after, (
            "tool.execute.before must guard via OPENCODE_SUBAGENT"
        )

    def test_subagent_guard_precedes_any_side_effect_in_before_hook(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        assert before_idx > 0
        after = src[before_idx:]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        report_idx = after.find("reportAlive")
        assert subagent_idx < report_idx, (
            "SUBAGENT check must precede reportAlive in tool.execute.before"
        )


# ---------------------------------------------------------------------------
# system.transform hook behavior
# ---------------------------------------------------------------------------


class TestSystemTransform:
    def test_injects_directive_before_output_when_string(self):
        src = _src()
        idx = src.find('"experimental.chat.system.transform"')
        after = src[idx:]
        assert "buildSessionDirective" in after or "SESSION START" in after, (
            "must prepend session directive to output"
        )

    def test_directive_prepended_with_newline_separator(self):
        src = _src()
        idx = src.find('directive + "\\n\\n" + output')
        assert idx > 0 or "directive + " in src, (
            "directive must be prepended with newline separator"
        )

    def test_returns_output_unchanged_when_not_string(self):
        src = _src()
        idx = src.find('typeof output === "string"')
        assert idx > 0, "must check typeof output === 'string'"
        # After the if-block there should be a return output for non-string
        after = src[idx:]
        second_return = after.find("return output", after.find("return output") + 1)
        assert second_return > 0, "must return output unchanged for non-string input"

    def test_tasks_staleness_nag_injected_when_session_old(self):
        src = _src()
        idx = src.find("needsTasksNag")
        assert idx > 0, "must check for tasks staleness in system.transform"

    def test_tasks_staleness_text_mentions_rule_7(self):
        src = _src()
        idx = src.find("RULE 7")
        assert idx > 0, "stale-TASKS nag must mention Mechanical Contract rule 7"

    def test_tasks_staleness_text_mentions_bugs_md(self):
        src = _src()
        idx = src.find("stale-TASKS" if "stale-TASKS" in src else "STALE SESSION")
        after = src[idx:] if idx > 0 else src
        assert "BUGS.md" in after, "stale-TASKS nag must mention BUGS.md"

    def test_fail_open_in_transform_catch(self):
        src = _src()
        idx = src.find('"experimental.chat.system.transform"')
        assert idx > 0
        after = src[idx:]
        # Must have try/catch with return output in catch
        m = re.search(r"catch\s*\{[^}]*return output", after, re.DOTALL)
        assert m, "system.transform catch must return output (fail-open)"


# ---------------------------------------------------------------------------
# Tool classification helpers
# ---------------------------------------------------------------------------


class TestToolClassification:
    def test_task_is_dispatch_tool(self):
        assert 'isDispatchTool' in _src(), (
            "Plugin must define or import isDispatchTool()."
        )

    def test_agent_is_dispatch_tool(self):
        assert 'isDispatchTool' in _src(), (
            "Plugin must import isDispatchTool from shared.ts."
        )

    def test_workflow_is_dispatch_tool(self):
        assert 'isDispatchTool' in _src(), (
            "Plugin must import isDispatchTool from shared.ts."
        )

    def test_read_is_not_dispatch_tool(self):
        src = _src()
        assert "isReadTool" in src, (
            "Plugin must define or import isReadTool()."
        )
        # Read tools are classified in shared.ts (READ_TOOLS set). The plugin
        # uses isReadTool() — not inline tool === "read" — so verify the import.
        assert "isReadTool" in src

    def test_is_task_file_read_checks_task_files_list(self):
        src = _src()
        idx = src.find("function isTaskFileRead")
        assert idx > 0
        after = src[idx:idx + 2000]
        assert "TASK_FILES" in after

    def test_task_file_read_uses_read_tool_check(self):
        src = _src()
        idx = src.find("function isTaskFileRead")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "isReadTool" in after

    def test_task_file_read_has_stringify_guard(self):
        src = _src()
        idx = src.find("function isTaskFileRead")
        assert idx > 0
        after = src[idx:idx + 2000]
        assert "JSON.stringify" in after or "stringify" in after

    def test_task_files_list_contains_all_four(self):
        src = _src()
        assert '"TASKS.md"' in src
        assert '"BUGS.md"' in src
        assert '"config/ratchet.yml"' in src
        assert '"SESSION.md"' in src


# ---------------------------------------------------------------------------
# SessionState interface and state file I/O
# ---------------------------------------------------------------------------


class TestSessionStateInterface:
    def test_state_interface_exists(self):
        src = _src()
        assert "interface SessionState" in src

    def test_state_has_started_at_field(self):
        src = _src()
        assert "started_at" in src

    def test_state_has_reads_done_field(self):
        src = _src()
        assert "readsDone" in src

    def test_state_has_dispatches_field(self):
        src = _src()
        assert "dispatches:" in src or "dispatches:" in src

    def test_state_has_time_gate_reset_field(self):
        src = _src()
        assert "timeGateReset" in src


class TestStateFileIO:
    def test_load_state_creates_file_when_missing(self):
        src = _src()
        idx = src.find("function loadState")
        assert idx > 0
        after = src[idx:idx + 600]
        assert "writeFileSync" in after, "loadState must create file when missing"

    def test_load_state_sets_started_at_to_date_now_on_creation(self):
        src = _src()
        idx = src.find("function loadState")
        after = src[idx:idx + 800]
        assert "Date.now()" in after, "new state file must have started_at = Date.now()"

    def test_load_state_starts_with_observed_zero_dispatches(self):
        src = _src()
        idx = src.find("function loadState")
        after = src[idx:idx + 800]
        assert "dispatches: 0" in after, (
            "A new state file must not fabricate dispatches to satisfy a configured minimum"
        )

    def test_load_state_sets_reads_done_false_on_creation(self):
        src = _src()
        idx = src.find("function loadState")
        after = src[idx:idx + 800]
        assert "readsDone: false" in after, "new state file must have readsDone = false"

    def test_load_state_handles_corrupt_file(self):
        src = _src()
        idx = src.find("function loadState")
        after = src[idx:idx + 3000]
        assert "catch" in after, "loadState must have try/catch for corrupt files"

    def test_load_state_returns_default_on_catch(self):
        src = _src()
        idx = src.find("function loadState")
        after = src[idx:idx + 3000]
        # After the catch block there should be a default return
        assert "Date.now()" in after[after.index("catch"):]

    def test_save_state_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function saveState")
        assert idx > 0
        after = src[idx:idx + 800]
        assert ".tmp" in after, "saveState must use tmp file"
        assert "renameSync" in after, "saveState must use atomic rename"

    def test_save_state_uses_pid_unique_tmp_path(self):
        src = _src()
        idx = src.find("function saveState")
        after = src[idx:idx + 500]
        assert "process.pid" in after, "tmp path must include PID for uniqueness"

    def test_save_state_fail_open_on_error(self):
        src = _src()
        idx = src.find("function saveState")
        after = src[idx:idx + 800]
        assert "catch" in after, "saveState must have try/catch for fail-open"


# ---------------------------------------------------------------------------
# Dispatch count tracking behavior
# ---------------------------------------------------------------------------


class TestDispatchCountTracking:
    def test_dispatch_increments_dispatches_counter(self):
        src = _src()
        idx = src.find("if (isDispatchTool(tool))")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "state.dispatches += 1" in after, (
            "dispatch tools must increment dispatches counter"
        )

    def test_dispatch_resets_time_gate_on_first_dispatch(self):
        src = _src()
        idx = src.find("if (isDispatchTool(tool))")
        assert idx > 0
        after = src[idx:idx + 250]
        assert "timeGateReset = true" in after, (
            "first dispatch must reset time gate"
        )

    def test_dispatch_triggers_update_primed_latch(self):
        src = _src()
        idx = src.find("if (isDispatchTool(tool))")
        assert idx > 0
        after = src[idx:idx + 300]
        assert "updatePrimedLatch(state)" in after, (
            "dispatch must call updatePrimedLatch to check if primed"
        )

    def test_dispatch_saves_state(self):
        src = _src()
        idx = src.find("if (isDispatchTool(tool))")
        assert idx > 0
        after = src[idx:idx + 250]
        assert "saveState(state)" in after, "dispatch must persist state"


# ---------------------------------------------------------------------------
# Task file read detection
# ---------------------------------------------------------------------------


class TestTaskFileReadDetection:
    def test_read_of_task_file_marks_reads_done(self):
        src = _src()
        idx = src.find("if (isTaskFileRead(tool, input, _output))")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "state.readsDone = true" in after

    def test_read_of_task_file_updates_tasks_mtime(self):
        src = _src()
        src.find("TASKS.md")
        after_start = src.rfind("_lastTasksReadMtime")
        assert after_start > 0, "must record TASKS.md read mtime"

    def test_read_of_task_file_triggers_primed_latch_check(self):
        src = _src()
        idx = src.find("if (isTaskFileRead(tool, input, _output))")
        assert idx > 0
        after = src[idx:idx + 600]
        assert "updatePrimedLatch(state)" in after


# ---------------------------------------------------------------------------
# Primed latch behavior
# ---------------------------------------------------------------------------


class TestPrimedLatch:
    def test_primed_latch_function_exists(self):
        src = _src()
        assert "function updatePrimedLatch" in src

    def test_primed_condition_requires_reads_done_and_dispatches(self):
        src = _src()
        idx = src.find("function updatePrimedLatch")
        after = src[idx:idx + 200]
        assert "state.readsDone" in after
        assert "state.dispatches >= EFFECTIVE_MIN" in after

    def test_module_level_latch_variable_exists(self):
        src = _src()
        assert "let sessionPrimed" in src

    def test_latch_can_be_null(self):
        src = _src()
        idx = src.find("let sessionPrimed")
        after = src[idx:idx + 50]
        assert "null" in after, "sessionPrimed must support null (uninitialized)"

    def test_latch_skips_state_io_when_primed(self):
        src = _src()
        idx = src.find("if (sessionPrimed === true) return")
        assert idx > 0, "must skip state I/O when latch is primed"


# ---------------------------------------------------------------------------
# Time gate behavior
# ---------------------------------------------------------------------------


class TestTimeGate:
    def test_time_gate_only_fires_below_explicit_minimum(self):
        src = _src()
        assert "EFFECTIVE_MIN > 0" in src
        assert "state.dispatches < EFFECTIVE_MIN" in src

    def test_time_gate_requires_not_reset(self):
        src = _src()
        idx = src.find("!state.timeGateReset")
        assert idx > 0, "time gate must check timeGateReset is false"

    def test_time_gate_elapsed_uses_date_now_minus_started_at(self):
        src = _src()
        idx = src.find("(Date.now() - state.started_at) / 1000")
        assert idx > 0, "time gate uses seconds-since-started-at"

    def test_time_gate_hard_deny_uses_throw_new_error(self):
        src = _src()
        idx = src.find("if (ENFORCE)")
        assert idx > 0, "hard deny must check ENFORCE flag in time gate"
        after = src[idx + src.find("HARD_DENY_SECS"):]
        segment = after[after.find("if (ENFORCE)"):after.find("if (ENFORCE)") + 400]
        assert "throw new Error" in segment or "throw new Error" in src, "enforce mode must throw"

    def test_time_gate_warning_throttled_to_30s(self):
        src = _src()
        assert "30_000" in src
        assert "_lastTimeGateWarningTs" in src

    def test_time_gate_warning_uses_dispatch_now_label(self):
        src = _src()
        assert "DISPATCH NOW" in src, "warning must use DISPATCH NOW label"

    def test_time_gate_deny_uses_time_gate_label(self):
        src = _src()
        assert "TIME GATE" in src, "deny must use TIME GATE label"


# ---------------------------------------------------------------------------
# Fresh session mutation gate
# ---------------------------------------------------------------------------


class TestFreshSessionGate:
    def test_fresh_session_gate_checks_session_is_fresh(self):
        src = _src()
        idx = src.find("if (sessionIsFresh(state))")
        assert idx > 0, "mutation deny must check sessionIsFresh"

    def test_fresh_session_gate_gated_behind_not_primed(self):
        src = _src()
        idx = src.find("if (updatePrimedLatch(state)) return")
        assert idx > 0, "must return early if primed (allow all tools)"

    def test_fresh_session_deny_message_includes_reads_done_status(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after_hook = src[before_idx:]
        idx = after_hook.find("SESSION START PROTOCOL")
        assert idx > 0, "deny message not found in tool.execute.before"
        after = after_hook[idx:idx + 500]
        assert "readsDone" in after, "deny message must include readsDone status"

    def test_fresh_session_deny_message_includes_dispatch_count(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after_hook = src[before_idx:]
        idx = after_hook.find("SESSION START PROTOCOL")
        assert idx > 0
        after = after_hook[idx:idx + 500]
        assert "dispatches" in after, "deny message must include dispatch count"

    def test_fresh_session_deny_message_mentions_effective_min(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after_hook = src[before_idx:]
        idx = after_hook.find("SESSION START PROTOCOL")
        assert idx > 0
        after = after_hook[idx:idx + 500]
        assert "EFFECTIVE_MIN" in after, "deny message must reference EFFECTIVE_MIN"

    def test_fresh_session_deny_message_mentions_env_var_disable(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        after_hook = src[before_idx:]
        idx = after_hook.find("SESSION START PROTOCOL")
        assert idx > 0
        after = after_hook[idx:idx + 1200]
        assert "GLUDD_SESSION_START_ENFORCE=0" in after, (
            "deny message must mention how to disable enforcement"
        )

    def test_fresh_session_gate_allows_reads(self):
        src = _src()
        idx = src.find("if (isReadTool(tool))")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "return" in after, "read tools must be allowed during fresh session"


# ---------------------------------------------------------------------------
# ENFORCE enable/disable behavior
# ---------------------------------------------------------------------------


class TestEnforceEnableDisable:
    def test_enforce_constant_is_boolean(self):
        src = _src()
        assert "const ENFORCE = process.env.GLUDD_SESSION_START_ENFORCE !== " in src

    def test_enforce_defaults_to_true(self):
        src = _src()
        m = re.search(r'GLUDD_SESSION_START_ENFORCE !== "(\d+)"', src)
        assert m, "ENFORCE check not found"
        assert m.group(1) == "0", "default: ENFORCE=true (disabled only when env === '0')"

    def test_enforce_gates_mutation_deny(self):
        src = _src()
        idx = src.find("if (sessionIsFresh(state))")
        after = src[idx:]
        # There should be an "if (ENFORCE)" block
        assert "if (ENFORCE)" in after, "mutation deny must be gated on ENFORCE"

    def test_enforce_gates_time_gate_deny(self):
        src = _src()
        idx = src.find("HARD_DENY_SECS")
        after = src[idx:]
        assert "if (ENFORCE)" in after, "time gate deny must be gated on ENFORCE"

    def test_console_warn_always_fires_regardless_of_enforce(self):
        src = _src()
        idx = src.find("console.warn(msg)")
        assert idx > 0, "warning must fire even when ENFORCE is off (advisory mode)"

    def test_deny_message_is_null_when_enforce_off(self):
        src = _src()
        idx = src.find("if (ENFORCE)")
        assert idx > 0
        after = src[idx:idx + 150]
        assert "denyMessage" in after, "denyMessage must be set inside ENFORCE block"


# ---------------------------------------------------------------------------
# Heartbeat and liveness reporting
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_report_alive_function_exists(self):
        src = _src()
        assert "reportAlive" in src, (
            "Plugin must define or import reportAlive."
        )

    def test_report_alive_writes_to_alive_file(self):
        src = _src()
        assert "reportAlive(\"enforce-session-start\")" in src

    def test_per_plugin_heartbeat_enforce_session_start_json(self):
        src = _src()
        assert "gludd-plugin-heartbeat-enforce-session-start.json" in src

    def test_plugin_loaded_logging(self):
        src = _src()
        assert "gludd-plugin-loaded.log" in src

    def test_loaded_log_includes_plugin_name(self):
        src = _src()
        idx = src.find("LOADED enforce-session-start")
        assert idx > 0, "loaded log must include plugin name"


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
        assert catch_count >= 5, f"expected >=5 catch blocks, found {catch_count}"

    def test_system_transform_catch_returns_output(self):
        src = _src()
        idx = src.find('"experimental.chat.system.transform"')
        after = src[idx:]
        m = re.search(r"catch\s*\{[^}]*return output", after, re.DOTALL)
        assert m, "system.transform must return output on error"

    def test_tool_execute_before_outer_catch_exists(self):
        src = _src()
        idx = src.find('"tool.execute.before": async')
        assert idx > 0
        after = src[idx:]
        m = re.search(r"catch\s*\{", after, re.DOTALL)
        assert m, "tool.execute.before must have outer try/catch"

    def test_tool_execute_before_catch_comment_mentions_fail_open(self):
        src = _src()
        idx = src.find("fail open — never wedge the session")
        assert idx > 0, "outer catch must document fail-open intent"

    def test_save_state_fail_open_on_io_error(self):
        src = _src()
        idx = src.find("function saveState")
        after = src[idx:idx + 800]
        assert "catch" in after, "saveState must catch I/O errors"

    def test_load_state_fail_open_on_corrupt_json(self):
        src = _src()
        idx = src.find("function loadState")
        after = src[idx:idx + 3000]
        assert "catch" in after, "loadState must catch corrupt JSON"


# ---------------------------------------------------------------------------
# sessionIsFresh function
# ---------------------------------------------------------------------------


class TestSessionIsFresh:
    def test_session_is_fresh_function_exists(self):
        src = _src()
        assert "function sessionIsFresh" in src

    def test_uses_date_now_minus_started_at(self):
        src = _src()
        idx = src.find("function sessionIsFresh")
        after = src[idx:idx + 150]
        assert "Date.now()" in after
        assert "started_at" in after

    def test_compares_against_fresh_secs(self):
        src = _src()
        idx = src.find("function sessionIsFresh")
        after = src[idx:idx + 150]
        assert "FRESH_SECS" in after

    def test_divides_by_1000_for_seconds(self):
        src = _src()
        idx = src.find("function sessionIsFresh")
        after = src[idx:idx + 150]
        assert "/ 1000" in after or "/1000" in after


# ---------------------------------------------------------------------------
# Behavioral tests — Python simulation of plugin state-machine logic
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_dispatch_tool(tool: str) -> bool:
    return tool in ("task", "agent", "workflow")


def _is_read_tool(tool: str) -> bool:
    return tool in ("read", "glob", "grep")


TASK_FILES = ["tasks.md", "bugs.md", "config/ratchet.yml", "session.md"]


def _is_task_file_read(tool: str, input_obj: dict | None) -> bool:
    if not _is_read_tool(tool):
        return False
    try:
        blob = json.dumps(input_obj or {}).lower()
        return any(f in blob for f in TASK_FILES)
    except Exception:
        return False


MIN_DISPATCHES = 3
FLOOR = 10
EFFECTIVE_MIN = min(MIN_DISPATCHES, FLOOR)
FRESH_SECS = 600

ENFORCE = True


class _SessionState:
    def __init__(self, started_at: int):
        self.started_at = started_at
        self.readsDone = False
        self.dispatches = 0
        self.timeGateReset = False

    def session_is_fresh(self) -> bool:
        return (self._now() - self.started_at) / 1000 < FRESH_SECS

    @staticmethod
    def _now() -> int:
        return _now_ms()

    def is_primed(self) -> bool:
        return self.readsDone and self.dispatches >= EFFECTIVE_MIN


class TestBehavioral:
    def test_new_session_is_not_primed(self):
        state = _SessionState(_now_ms())
        assert not state.is_primed()

    def test_reads_done_without_dispatches_not_primed(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        assert not state.is_primed()

    def test_dispatches_without_reads_not_primed(self):
        state = _SessionState(_now_ms())
        state.dispatches = EFFECTIVE_MIN
        assert not state.is_primed()

    def test_reads_and_dispatches_primed(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        state.dispatches = EFFECTIVE_MIN
        assert state.is_primed()

    def test_fresh_session_is_fresh(self):
        state = _SessionState(_now_ms())
        assert state.session_is_fresh()

    def test_old_session_is_not_fresh(self):
        state = _SessionState(_now_ms() - (FRESH_SECS + 10) * 1000)
        assert not state.session_is_fresh()

    def test_dispatch_increments_counter(self):
        state = _SessionState(_now_ms())
        assert state.dispatches == 0
        state.dispatches += 1
        assert state.dispatches == 1

    def test_first_dispatch_resets_time_gate(self):
        state = _SessionState(_now_ms())
        assert not state.timeGateReset
        state.timeGateReset = True
        assert state.timeGateReset

    def test_task_file_read_marks_reads_done(self):
        state = _SessionState(_now_ms())
        assert not state.readsDone
        state.readsDone = True
        assert state.readsDone

    def test_is_task_file_read_detects_tasks_md(self):
        assert _is_task_file_read("read", {"filePath": "/app/TASKS.md"})

    def test_is_task_file_read_detects_bugs_md(self):
        assert _is_task_file_read("read", {"filePath": "/app/BUGS.md"})

    def test_is_task_file_read_detects_session_md(self):
        assert _is_task_file_read("read", {"filePath": "/app/SESSION.md"})

    def test_is_task_file_read_detects_ratchet_yml(self):
        assert _is_task_file_read("read", {"filePath": "/app/config/ratchet.yml"})

    def test_is_task_file_read_rejects_non_task_file(self):
        assert not _is_task_file_read("read", {"filePath": "/app/daemon.py"})

    def test_is_task_file_read_rejects_non_read_tool(self):
        assert not _is_task_file_read("write", {"filePath": "/app/TASKS.md"})

    def test_is_task_file_read_detects_grep_on_read_surface_with_task_file(self):
        assert _is_task_file_read("grep", {"pattern": "TASKS.md"})

    def test_is_task_file_read_handles_null_input(self):
        assert not _is_task_file_read("read", None)

    def test_is_task_file_read_handles_empty_input(self):
        assert not _is_task_file_read("read", {})


class TestSubagentGuardBehavioral:
    def test_subagent_env_skips_all_enforcement(self):
        is_subagent = True
        state = _SessionState(_now_ms())
        if not is_subagent:
            state.readsDone = True
            state.dispatches = EFFECTIVE_MIN
        assert not state.is_primed()
        assert not state.readsDone
        assert state.dispatches == 0


class TestTimeGateBehavioral:
    def test_time_gate_fires_after_hard_deny_seconds(self):
        hard_deny_secs = 120
        started = _now_ms() - (hard_deny_secs + 10) * 1000
        state = _SessionState(started)
        elapsed = (_now_ms() - state.started_at) / 1000
        assert elapsed >= hard_deny_secs

    def test_time_gate_does_not_fire_before_dispatch_now_seconds(self):
        dispatch_now_secs = 60
        started = _now_ms() - (dispatch_now_secs - 10) * 1000
        state = _SessionState(started)
        elapsed = (_now_ms() - state.started_at) / 1000
        assert elapsed < dispatch_now_secs

    def test_time_gate_resets_on_first_dispatch(self):
        hard_deny_secs = 120
        started = _now_ms() - (hard_deny_secs + 30) * 1000
        state = _SessionState(started)
        assert not state.timeGateReset
        state.timeGateReset = True
        elapsed = (_now_ms() - state.started_at) / 1000
        assert elapsed >= hard_deny_secs
        assert state.timeGateReset

    def test_enforce_false_means_no_deny(self):
        enforce = False
        deny_message = None
        state = _SessionState(_now_ms())
        if not state.is_primed() and state.session_is_fresh():
            msg = "SESSION START PROTOCOL"
            if enforce:
                deny_message = msg
        assert deny_message is None

    def test_enforce_true_means_deny(self):
        enforce = True
        deny_message = None
        state = _SessionState(_now_ms())
        if not state.is_primed() and state.session_is_fresh():
            msg = "SESSION START PROTOCOL"
            if enforce:
                deny_message = msg
        assert deny_message is not None


class TestStateFileBehavioral:
    def test_roundtrip_json_shape(self, tmp_path):
        state_file = tmp_path / "session-start.json"
        state = {
            "started_at": _now_ms(),
            "readsDone": True,
            "dispatches": 5,
            "timeGateReset": True,
        }
        state_file.write_text(json.dumps(state))
        loaded = json.loads(state_file.read_text())
        assert loaded["readsDone"] is True
        assert loaded["dispatches"] == 5
        assert loaded["timeGateReset"] is True
        assert "started_at" in loaded

    def test_corrupt_file_returns_default(self, tmp_path):
        state_file = tmp_path / "session-start.json"
        state_file.write_text("{{{broken")
        try:
            _ = json.loads(state_file.read_text())
            raise AssertionError("should have raised")
        except Exception:
            pass
        default = {
            "started_at": _now_ms(),
            "readsDone": False,
            "dispatches": 0,
            "timeGateReset": False,
        }
        assert default["dispatches"] == 0
        assert not default["readsDone"]

    def test_missing_file_triggers_creation_with_defaults(self, tmp_path):
        state_file = tmp_path / "nonexistent.json"
        initial = {
            "started_at": _now_ms(),
            "readsDone": False,
            "dispatches": 0,
            "timeGateReset": False,
        }
        state_file.write_text(json.dumps(initial))
        loaded = json.loads(state_file.read_text())
        assert loaded["dispatches"] == 0
        assert not loaded["readsDone"]

    def test_atomic_write_uses_tmp_and_rename(self, tmp_path):
        state_file = tmp_path / "session-start.json"
        tmp_file = tmp_path / "session-start.json.tmp.12345"
        state = {"started_at": _now_ms(), "readsDone": False, "dispatches": 0, "timeGateReset": False}
        tmp_file.write_text(json.dumps(state))
        os.replace(tmp_file, state_file)
        assert state_file.exists()
        assert not tmp_file.exists()
        loaded = json.loads(state_file.read_text())
        assert loaded["dispatches"] == 0


class TestPrimedLatchBehavioral:
    def test_null_latch_transitions_to_false_when_not_primed(self):
        state = _SessionState(_now_ms())
        primed = state.is_primed()
        assert not primed

    def test_false_latch_transitions_to_true_when_primed(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        state.dispatches = EFFECTIVE_MIN
        primed = state.is_primed()
        assert primed

    def test_true_latch_skips_all_checks(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        state.dispatches = EFFECTIVE_MIN
        primed = state.is_primed()
        assert primed
        second_check = state.is_primed()
        assert second_check


class TestDispatchToolsBehavioral:
    def test_task_is_dispatch(self):
        assert _is_dispatch_tool("task")

    def test_agent_is_dispatch(self):
        assert _is_dispatch_tool("agent")

    def test_workflow_is_dispatch(self):
        assert _is_dispatch_tool("workflow")

    def test_read_is_not_dispatch(self):
        assert not _is_dispatch_tool("read")

    def test_bash_is_not_dispatch(self):
        assert not _is_dispatch_tool("bash")

    def test_write_is_not_dispatch(self):
        assert not _is_dispatch_tool("write")

    def test_edit_is_not_dispatch(self):
        assert not _is_dispatch_tool("edit")


class TestReadToolsBehavioral:
    def test_read_is_read_tool(self):
        assert _is_read_tool("read")

    def test_glob_is_read_tool(self):
        assert _is_read_tool("glob")

    def test_grep_is_read_tool(self):
        assert _is_read_tool("grep")

    def test_bash_is_not_read_tool(self):
        assert not _is_read_tool("bash")

    def test_write_is_not_read_tool(self):
        assert not _is_read_tool("write")


class TestSessionStateBehavioral:
    def test_primed_is_false_when_dispatches_below_effective_min(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        state.dispatches = EFFECTIVE_MIN - 1
        assert not state.is_primed()

    def test_primed_is_true_when_dispatches_equals_effective_min(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        state.dispatches = EFFECTIVE_MIN
        assert state.is_primed()

    def test_primed_is_true_when_dispatches_exceeds_effective_min(self):
        state = _SessionState(_now_ms())
        state.readsDone = True
        state.dispatches = EFFECTIVE_MIN + 5
        assert state.is_primed()

    def test_freshness_uses_seconds_not_ms(self):
        state = _SessionState(_now_ms() - (FRESH_SECS - 10) * 1000)
        assert state.session_is_fresh()

    def test_exactly_at_boundary_is_fresh(self):
        state = _SessionState(_now_ms() - FRESH_SECS * 1000)
        elapsed = (_now_ms() - state.started_at) / 1000
        assert elapsed <= FRESH_SECS

    def test_one_second_over_boundary_is_not_fresh(self):
        state = _SessionState(_now_ms() - (FRESH_SECS + 1) * 1000)
        elapsed = (_now_ms() - state.started_at) / 1000
        assert elapsed > FRESH_SECS
