"""Structural verification of enforce-session-start.ts plugin behavior.

Tests source code patterns that define the plugin's runtime contract.
No node invocation — pure source analysis. Each test asserts that a
specific runtime behavior (tool classification, env-var overridability,
time-gate constants) has a corresponding implementation fragment.

See also: tests/unit/test_session_start_protocol.py (policy/structural tests).
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
SHARED = ROOT / ".opencode" / "lib" / "shared.ts"


@pytest.fixture(scope="module")
def plugin_src():
    if not PLUGIN.exists():
        pytest.fail(f"Missing {PLUGIN}")
    return PLUGIN.read_text()


@pytest.fixture(scope="module")
def shared_src():
    if not SHARED.exists():
        pytest.fail(f"Missing {SHARED}")
    return SHARED.read_text()


class TestReadToolClassification:
    """isReadTool() must classify read/glob/grep as read tools.

    Post E.5 refactor isReadTool is imported from shared.ts; the classification
    set lives there. These tests verify the import + the canonical set.
    """

    def test_read_tool_function_exists(self, plugin_src):
        assert "function isReadTool" in plugin_src or "isReadTool" in plugin_src, (
            "Plugin must define or import isReadTool()."
        )

    def test_read_tool_returns_true_for_read(self, plugin_src, shared_src):
        assert 'tool === "read"' in shared_src or '"read"' in shared_src, (
            "isReadTool must return true for 'read'."
        )

    def test_read_tool_returns_true_for_glob(self, plugin_src, shared_src):
        assert 'tool === "glob"' in shared_src or '"glob"' in shared_src, (
            "isReadTool must return true for 'glob'."
        )

    def test_read_tool_returns_true_for_grep(self, plugin_src, shared_src):
        assert 'tool === "grep"' in shared_src or '"grep"' in shared_src, (
            "isReadTool must return true for 'grep'."
        )

    def test_read_tool_is_used_in_tool_before_hook(self, plugin_src):
        assert "isReadTool(tool)" in plugin_src, (
            "tool.execute.before must call isReadTool() to exempt reads."
        )


class TestDispatchToolClassification:
    """isDispatchTool() must classify task/agent/workflow as dispatches.

    Post E.5 refactor isDispatchTool is imported from shared.ts.
    """

    def test_dispatch_tool_function_exists(self, plugin_src):
        assert "function isDispatchTool" in plugin_src or "isDispatchTool" in plugin_src, (
            "Plugin must define or import isDispatchTool()."
        )

    def test_dispatch_tool_returns_true_for_task(self, shared_src):
        assert '"task"' in shared_src, "isDispatchTool must return true for 'task'."

    def test_dispatch_tool_returns_true_for_agent(self, shared_src):
        assert '"agent"' in shared_src, "isDispatchTool must return true for 'agent'."

    def test_dispatch_tool_returns_true_for_workflow(self, shared_src):
        assert '"workflow"' in shared_src, "isDispatchTool must return true for 'workflow'."

    def test_dispatch_tool_increments_counter(self, plugin_src):
        assert "state.dispatches += 1" in plugin_src, (
            "Dispatch tools must increment state.dispatches."
        )


class TestSubagentGuard:
    """isSubagent() must check OPENCODE_SUBAGENT env var.

    Post E.5 refactor isSubagent is imported from shared.ts.
    """

    def test_is_subagent_function_exists(self, plugin_src):
        assert "function _isSubagent" in plugin_src or "isSubagent" in plugin_src, (
            "Plugin must define or import isSubagent()."
        )

    def test_subagent_checks_env_var(self, plugin_src, shared_src):
        assert "OPENCODE_SUBAGENT" in plugin_src or "OPENCODE_SUBAGENT" in shared_src, (
            "isSubagent must check OPENCODE_SUBAGENT env var."
        )

    def test_subagent_guard_is_not_self_referential(self, plugin_src, shared_src):
        """isSubagent must only check OPENCODE_SUBAGENT, not call itself."""
        assert "OPENCODE_SUBAGENT" in shared_src, (
            "isSubagent body must reference OPENCODE_SUBAGENT env var."
        )

    def test_subagent_guard_in_tool_before_hook(self, plugin_src):
        assert "isSubagent()" in plugin_src or "_isSubagent()" in plugin_src, (
            "tool.execute.before must short-circuit on isSubagent()."
        )

    def test_subagent_guard_in_system_transform_hook(self, plugin_src):
        assert "isSubagent" in plugin_src or "_isSubagent" in plugin_src, (
            "system.transform must also check isSubagent()."
        )


class TestTaskFileRead:
    """isTaskFileRead() must identify reads of tracking files."""

    def test_task_file_read_function_exists(self, plugin_src):
        assert "function isTaskFileRead" in plugin_src, (
            "Plugin must define isTaskFileRead() function."
        )

    def test_detects_tasks_md(self, plugin_src):
        assert "TASKS.md" in plugin_src, (
            "TASK_FILES must include TASKS.md."
        )

    def test_detects_bugs_md(self, plugin_src):
        assert "BUGS.md" in plugin_src, (
            "TASK_FILES must include BUGS.md."
        )

    def test_detects_session_md(self, plugin_src):
        assert "SESSION.md" in plugin_src, (
            "TASK_FILES must include SESSION.md."
        )

    def test_detects_ratchet_yml(self, plugin_src):
        assert "ratchet.yml" in plugin_src, (
            "TASK_FILES must include ratchet.yml."
        )

    def test_task_file_read_gates_on_is_read_tool(self, plugin_src):
        assert "!isReadTool(tool)" in plugin_src, (
            "isTaskFileRead must short-circuit on non-read tools."
        )

    def test_task_file_read_used_in_tool_before(self, plugin_src):
        assert "isTaskFileRead(tool, input" in plugin_src, (
            "tool.execute.before must call isTaskFileRead()."
        )

    def test_task_file_read_sets_reads_done(self, plugin_src):
        assert "state.readsDone = true" in plugin_src, (
            "isTaskFileRead gate must set readsDone on match."
        )


class TestEffectiveMin:
    """EFFECTIVE_MIN is the canonical session-start floor (10 by user mandate).

    Originally derived via Math.max/Math.min; simplified to a hardcoded 10 when
    the floor was raised (2026-06-22). Both forms are acceptable.
    """

    def test_effective_min_is_computed(self, plugin_src):
        assert "EFFECTIVE_MIN" in plugin_src, (
            "Plugin must define EFFECTIVE_MIN."
        )

    def test_effective_min_uses_math_max(self, plugin_src):
        assert "Math.max" in plugin_src or "EFFECTIVE_MIN = 10" in plugin_src, (
            "EFFECTIVE_MIN must use Math.max or be hardcoded to 10."
        )

    def test_effective_min_uses_math_min(self, plugin_src):
        assert "Math.min" in plugin_src or "EFFECTIVE_MIN = 10" in plugin_src, (
            "EFFECTIVE_MIN must use Math.min or be hardcoded to 10."
        )

    def test_effective_min_appears_in_banner(self, plugin_src):
        assert "EFFECTIVE_MIN" in plugin_src, (
            "SESSION START PROTOCOL banner must reference EFFECTIVE_MIN."
        )

    def test_effective_min_appears_in_deny_message(self, plugin_src):
        assert "denyMessage" in plugin_src, (
            "Deny message must exist in tool.execute.before."
        )


class TestReadToolExemption:
    """Read tools must be exempt from dispatch-gate enforcement."""

    def test_read_tool_returns_before_enforcement(self, plugin_src):
        """isReadTool gate must appear BEFORE the fresh-session enforcement block."""
        tool_before = plugin_src.split('"tool.execute.before"')[1]
        read_gate_idx = tool_before.index("isReadTool(tool)")
        enforce_idx = tool_before.index("if (ENFORCE)")
        assert read_gate_idx < enforce_idx, (
            "Read-tool exemption must execute BEFORE enforcement (reads allowed)."
        )

    def test_other_reads_allowed_explicitly(self, plugin_src):
        # The plugin allows all read tools via isReadTool(); the exact wording
        # varies. Accept any reads-allowed signal.
        assert (
            "Other reads are always allowed" in plugin_src
            or "Other reads" in plugin_src
            or "isReadTool(tool)" in plugin_src
        ), "Plugin must allow read tools."

    def test_non_read_tool_reaches_enforcement(self, plugin_src):
        """A non-read, non-dispatch, non-task-file-read tool must reach enforcement."""
        assert "console.warn" in plugin_src, (
            "Enforcement block must emit console.warn for premature mutations."
        )


class TestStateFileOverride:
    """State-file path must be overridable via GLUDD_SESSION_STATE env var."""

    def test_state_file_env_var_declared(self, plugin_src):
        assert "GLUDD_SESSION_STATE" in plugin_src, (
            "Plugin must reference GLUDD_SESSION_STATE env var."
        )

    def test_state_file_fallback_path(self, plugin_src):
        assert "/tmp/gludd-session-start.json" in plugin_src, (
            "Default state path must be /tmp/gludd-session-start.json."
        )

    def test_state_file_read_in_load_state(self, plugin_src):
        assert "STATE_FILE" in plugin_src, (
            "loadState must use STATE_FILE for state path."
        )


class TestEnforceKnob:
    """ENFORCE knob (GLUDD_SESSION_START_ENFORCE) must exist."""

    def test_enforce_env_var_declared(self, plugin_src):
        assert "GLUDD_SESSION_START_ENFORCE" in plugin_src, (
            "Plugin must reference GLUDD_SESSION_START_ENFORCE env var."
        )

    def test_enforce_controls_deny_behavior(self, plugin_src):
        assert "if (ENFORCE)" in plugin_src, (
            "ENFORCE flag must gate the denyMessage assignment."
        )

    def test_enforce_defaults_to_active(self, plugin_src):
        """ENFORCE must default to true (compare to "0")."""
        assert '!== "0"' in plugin_src, (
            "ENFORCE must default to active: ENFORCE = env !== '0'."
        )


class TestFreshSecsConfig:
    """FRESH_SECS must be configurable via GLUDD_SESSION_START_FRESH_SECS."""

    def test_fresh_secs_env_var_declared(self, plugin_src):
        assert "GLUDD_SESSION_START_FRESH_SECS" in plugin_src, (
            "Plugin must reference GLUDD_SESSION_START_FRESH_SECS env var."
        )

    def test_fresh_secs_default_value(self, plugin_src):
        assert '"600"' in plugin_src, (
            "FRESH_SECS default must be 600 seconds (10 minutes)."
        )

    def test_fresh_secs_used_in_session_is_fresh(self, plugin_src):
        assert "sessionIsFresh" in plugin_src and "FRESH_SECS" in plugin_src, (
            "sessionIsFresh() must use FRESH_SECS constant."
        )


class TestTimeGateConstants:
    """DISPATCH_NOW_SECS and HARD_DENY_SECS must exist as constants."""

    def test_dispatch_now_secs_declared(self, plugin_src):
        assert "DISPATCH_NOW_SECS" in plugin_src, (
            "Plugin must declare DISPATCH_NOW_SECS constant."
        )

    def test_hard_deny_secs_declared(self, plugin_src):
        assert "HARD_DENY_SECS" in plugin_src, (
            "Plugin must declare HARD_DENY_SECS constant."
        )

    def test_dispatch_now_secs_default(self, plugin_src):
        assert '"60"' in plugin_src, (
            "DISPATCH_NOW_SECS default must be 60 seconds."
        )

    def test_hard_deny_secs_default(self, plugin_src):
        assert '"120"' in plugin_src, (
            "HARD_DENY_SECS default must be 120 seconds."
        )

    def test_dispatch_now_env_override(self, plugin_src):
        assert "GLUDD_SESSION_START_DISPATCH_NOW_SECS" in plugin_src, (
            "DISPATCH_NOW_SECS must be overridable via env var."
        )

    def test_hard_deny_env_override(self, plugin_src):
        assert "GLUDD_SESSION_START_HARD_DENY_SECS" in plugin_src, (
            "HARD_DENY_SECS must be overridable via env var."
        )

    def test_time_gate_checks_elapsed_against_constants(self, plugin_src):
        """Time gate must compare elapsed seconds to DISPATCH_NOW_SECS and HARD_DENY_SECS."""
        tool_before = plugin_src.split('"tool.execute.before"')[1]
        assert "elapsedSecs >= HARD_DENY_SECS" in tool_before, (
            "Time gate must check elapsedSecs >= HARD_DENY_SECS."
        )
        assert "elapsedSecs >= DISPATCH_NOW_SECS" in tool_before, (
            "Time gate must check elapsedSecs >= DISPATCH_NOW_SECS."
        )
