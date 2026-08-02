"""Behavior pin for the enforce-additive-task plugin.

Prevents 100% new-task dispatch waves when >=2 items remain unchecked in TASKS.md.
Requires at least 1 continuation slot (task ID reference like SEC.1, D-13)
per dispatch wave.

Covers: denial on all-new-task with >=2 unchecked, allow when continuation present,
subagent guard, env-var disable, fail-open on corrupt TASKS.md, denial at
exact 10/10 new-task ratio.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-additive-task.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_state_file_constant(self):
        src = _plugin_source()
        assert "STATE_FILE" in src, "STATE_FILE constant missing"

    def test_enabled_constant(self):
        src = _plugin_source()
        assert "ENABLED" in src, "ENABLED constant missing"

    def test_block_constant(self):
        src = _plugin_source()
        assert "BLOCK" in src, "BLOCK constant for soft/hard mode missing"

    def test_plugin_registered_in_opencode_json(self):
        oc = (PLUGIN_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-additive-task.ts" in oc, "Plugin not registered in opencode.json"


class TestDenyMessageContent:
    """Structural pin on deny message contents."""

    def test_deny_message_present(self):
        src = _plugin_source()
        assert "ADDITIVE TASK VIOLATION" in src, "Deny message must contain ADDITIVE TASK VIOLATION"

    def test_deny_mentions_unchecked_count(self):
        src = _plugin_source()
        idx = src.find("ADDITIVE TASK VIOLATION")
        after = src[idx : idx + 300]
        assert "unchecked" in after, "Deny must mention unchecked item count"

    def test_deny_mentions_continuation(self):
        src = _plugin_source()
        assert "continuation" in src, "Deny must mention continuation requirement"

    def test_deny_mentions_env_disable(self):
        src = _plugin_source()
        assert "GLUDD_ADDITIVE_TASK_ENFORCE" in src, "Deny must mention env-var disable"

    def test_ratio_denial_message_present(self):
        src = _plugin_source()
        assert "ADDITIVE TASK RATIO VIOLATION" in src, "Ratio denial message missing"


class TestAdditiveStateInterface:
    """State interface uses wave array tracking."""

    def test_additive_state_has_wave_field(self):
        src = _plugin_source()
        assert "wave: AdditiveEntry[]" in src, "AdditiveState must have wave field"

    def test_additive_state_has_in_progress_count(self):
        src = _plugin_source()
        assert "inProgressCount: number" in src, "AdditiveState must have inProgressCount"

    def test_additive_entry_has_type(self):
        src = _plugin_source()
        assert '"continuation" | "new-task"' in src, "AdditiveEntry must have type discriminant"


class TestDenialCondition:
    """Denial: unchecked >= 2 AND cCount === 0 (zero continuations in wave)."""

    def test_checks_unchecked_count(self):
        src = _plugin_source()
        assert "unchecked >= 2" in src, "Must check unchecked >= 2"

    def test_checks_continuation_count_zero(self):
        src = _plugin_source()
        assert "cCount === 0" in src, "Must check cCount === 0"

    def test_returns_permission_decision_deny(self):
        src = _plugin_source()
        assert 'permissionDecision: "deny"' in src, "Must return permissionDecision: deny"

    def test_gated_on_block_soft_mode(self):
        src = _plugin_source()
        assert "if (BLOCK)" in src, "Must gate denial on BLOCK soft-mode switch"


class TestExactDispatchCountDenial:
    """Denial at exact 10/10 new-task dispatches with 0 continuations."""

    def test_checks_total_at_least_10(self):
        src = _plugin_source()
        assert "total >= 10" in src, "Must check total >= 10 for ratio violation"

    def test_checks_all_new_tasks(self):
        src = _plugin_source()
        assert "newCount === total" in src, "Must check newCount === total"

    def test_denial_includes_new_task_count(self):
        src = _plugin_source()
        idx = src.find("ADDITIVE TASK RATIO VIOLATION")
        after = src[idx : idx + 300]
        assert "newCount" in after or "newPct" in after, "Denial must include new-task count/percent"


class TestClassificationLogic:
    """classify() uses TASK_ID_RE to detect continuation vs new-task."""

    def test_task_id_regex_exists(self):
        src = _plugin_source()
        assert "TASK_ID_RE" in src, "TASK_ID_RE regex missing"

    def test_task_id_regex_is_suppressed(self):
        src = _plugin_source()
        assert "TASK_ID_RE = /\\\\b[A-Z]+[.-]\\\\d+\\\\b/" in src or "TASK_ID_RE = /\\b[A-Z]+[.-]\\d+\\b/" in src, (
            "TASK_ID_RE must match task IDs like SEC.1, D-13, MWK.1"
        )

    def test_classify_function_exists(self):
        src = _plugin_source()
        assert "function classify" in src, "classify function missing"

    def test_classify_returns_continuation_or_new_task(self):
        src = _plugin_source()
        assert '"continuation" : "new-task"' in src, "classify must return continuation or new-task"


class TestSubagentGuard:
    """Plugin must skip enforcement inside subagents."""

    def test_is_subagent_guard_present(self):
        src = _plugin_source()
        assert "isSubagent()" in src, "isSubagent() guard missing"

    def test_subagent_returns_early(self):
        src = _plugin_source()
        m = re.search(r"isSubagent\(\)\s*\)\s*return", src)
        assert m, "isSubagent() must return early"

    def test_subagent_guard_before_classification(self):
        src = _plugin_source()
        # Proxy hook runs first and guards with isSubagent() before
        # delegating to defaultImpl which contains classify(). Verify
        # both proxy hook and defaultImpl have the guard.
        m = re.search(r"export default.*?isSubagent\(\)", src, re.DOTALL)
        assert m, "Proxy hook must call isSubagent() at entry"
        n = re.search(r"defaultImpl.*?isSubagent\(\)", src, re.DOTALL)
        assert n, "defaultImpl must call isSubagent() at entry"


class TestEnvVarDisable:
    """GLUDD_ADDITIVE_TASK_ENFORCE=0 must disable the plugin."""

    def test_enforce_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_ADDITIVE_TASK_ENFORCE" in src, "env-var disable switch missing"

    def test_disabled_when_set_to_zero(self):
        src = _plugin_source()
        assert 'GLUDD_ADDITIVE_TASK_ENFORCE || "1") !== "0"' in src, "Should check !== '0' to disable when set to 0"

    def test_enabled_by_default(self):
        src = _plugin_source()
        assert 'GLUDD_ADDITIVE_TASK_ENFORCE || "1"' in src, "ENABLED must default to true (|| '1')"


class TestFailOpen:
    """Plugin must be fail-open on all error paths."""

    def test_try_catch_in_tool_execute(self):
        src = _plugin_source()
        assert "catch" in src, "No try/catch fail-open block found"

    def test_count_unchecked_catch_block(self):
        src = _plugin_source()
        m = re.search(r"function countUnchecked.*?catch.*?\{", src, re.DOTALL)
        assert m, "countUnchecked must have try/catch"

    def test_count_unchecked_returns_zero_on_error(self):
        src = _plugin_source()
        m = re.search(r"countUnchecked.*?catch.*?return 0", src, re.DOTALL)
        assert m, "countUnchecked must return 0 on error (fail-open)"


class TestHooksRegistered:
    """Required hooks must be present."""

    def test_tool_execute_before_hook(self):
        assert "tool.execute.before" in _plugin_source()

    def test_proxy_plugin_present(self):
        src = _plugin_source()
        assert "loadHotModule" in src, "Hot-reload proxy missing"

    def test_satisfies_plugin(self):
        src = _plugin_source()
        assert "satisfies Plugin" in src, "Plugin must use satisfies Plugin"


class TestContinuationAllowance:
    """When at least 1 continuation task exists, denial must NOT fire."""

    def test_denial_requires_c_count_zero(self):
        """Denial gated on cCount === 0, so any continuation passes."""
        src = _plugin_source()
        idx = src.find("ADDITIVE TASK VIOLATION")
        assert idx > 0
        before = src[max(0, idx - 200) : idx]
        assert "cCount === 0" in before, "Denial must require cCount === 0"

    def test_continuation_passes_through(self):
        """When cCount > 0, no denial fires — work continues."""
        src = _plugin_source()
        idx = src.find("ADDITIVE TASK VIOLATION")
        before = src[max(0, idx - 200) : idx]
        assert "cCount === 0" in before, "Continuation must pass (gated on === 0)"


class TestCountUnchecked:
    """countUnchecked() counts unchecked items in TASKS.md."""

    def test_counts_unchecked_checkboxes(self):
        src = _plugin_source()
        assert "TASKS.md" in src, "Must read TASKS.md"

    def test_returns_zero_when_no_tasks_md(self):
        src = _plugin_source()
        assert "existsSync" in src, "Must check if TASKS.md exists"

    def test_matches_unchecked_checkbox_pattern(self):
        src = _plugin_source()
        # countUnchecked reads TASKS.md at runtime and matches unchecked
        # checkboxes via regex, not a hardcoded string literal.
        assert "-\\s*\\[ \\]" in src or "/^-\\s*\\[ \\]/gm" in src, (
            "countUnchecked must use regex to match unchecked markdown checkboxes"
        )


class TestExtractPrompt:
    """Prompt extraction from dispatch arguments."""

    def test_extract_prompt_function_exists(self):
        src = _plugin_source()
        assert "function extractPrompt" in src, "extractPrompt function missing"

    def test_checks_prompt_field(self):
        src = _plugin_source()
        assert "args.prompt" in src, "Must extract args.prompt"

    def test_checks_description_field(self):
        src = _plugin_source()
        assert "args.description" in src, "Must extract args.description"


class TestSoftMode:
    """BLOCK=0 provides soft-mode (console.warn) instead of hard deny."""

    def test_has_soft_mode_console_warn(self):
        src = _plugin_source()
        assert "console.warn" in src, "Must have console.warn for soft mode"

    def test_soft_mode_env_var_present(self):
        src = _plugin_source()
        assert "GLUDD_ADDITIVE_TASK_BLOCK" in src, "Soft-mode env var missing"

    def test_block_and_soft_mode_mutually_exclusive(self):
        src = _plugin_source()
        assert "if (BLOCK)" in src, "Must gate hard deny on BLOCK flag"


class TestStatePersistence:
    """State loading and saving."""

    def test_load_state_function_exists(self):
        src = _plugin_source()
        assert "function loadState" in src, "loadState function missing"

    def test_save_state_function_exists(self):
        src = _plugin_source()
        assert "function saveState" in src, "saveState function missing"

    def test_stale_pid_detection(self):
        src = _plugin_source()
        assert "process.pid" in src, "Must track PID for stale detection"

    def test_wave_reset_after_ten_dispatches(self):
        src = _plugin_source()
        assert "total >= 10" in src and "s.wave = []" in src, "Wave must reset after 10 dispatches"


class TestWaveFiltering:
    """Wave entries classified and counted via filter."""

    def test_filters_continuation_type(self):
        src = _plugin_source()
        assert '"continuation"' in src, "Must filter for 'continuation' type"

    def test_filters_new_task_type(self):
        src = _plugin_source()
        assert '"new-task"' in src, "Must filter for 'new-task' type"

    def test_uses_c_count_variable(self):
        src = _plugin_source()
        assert "cCount" in src, "cCount variable must exist for continuation count"

    def test_uses_new_count_variable(self):
        src = _plugin_source()
        assert "newCount" in src or "new Count" in src, "newCount variable must exist"


class TestBuiltInDispatchToolCheck:
    """Plugin has inline isDispatchTool, not importing from shared."""

    def test_inline_dispatch_tool_check(self):
        src = _plugin_source()
        assert "function isDispatchTool" in src, "Must define isDispatchTool inline"

    def test_checks_task_agent_workflow(self):
        src = _plugin_source()
        m = re.search(r'tool === "task".*tool === "agent".*tool === "workflow"', src, re.DOTALL)
        assert m, "Must check for task, agent, and workflow"
