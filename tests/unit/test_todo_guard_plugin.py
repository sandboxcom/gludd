"""Tests for the guardrail plugin.

The plugin (`.opencode/plugin/enforce-stop.ts`) prevents the recurring failure
mode where the agent dispatches N parallel subagents, receives N results, then
sends a text SUMMARY without codifying any of them -- dropping the work.
Formerly enforce-todos.ts; merged into enforce-stop.ts per AS.1 rewrite
(824 to 388 lines, 433 to 5 vocabulary patterns, state-based detection).

Two enforcement layers:
  * text.complete + session.idle -- when pending work exists AND the outgoing
    response is a text summary with no tool call, a directive blocks the stop.
  * tool.execute.before -- when a commit-shaped make target is invoked,
    state-based checks prevent commits while work is pending.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
_IMPL = ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"


def _src() -> str:
    """Read plugin source including impl file if it exists."""
    s = PLUGIN.read_text()
    if _IMPL.exists():
        s += "\n" + _IMPL.read_text()
    return s


class TestPluginFileExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-stop.ts must exist -- this plugin guarantees that every "
            "parallel subagent result is codified before the agent sends a "
            "terminal response."
        )

    def test_plugin_exports_default(self):
        src = _src()
        assert "export default" in src

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads((ROOT / "opencode.json").read_text())
        assert any("enforce-stop" in str(p) for p in cfg.get("plugin", [])), (
            "enforce-stop.ts must be registered in opencode.json plugin[] "
            "or it will never load."
        )


class TestPluginHookRegistration:
    def test_plugin_registers_text_complete(self):
        src = _src()
        assert "text.complete" in src, (
            "Plugin must register experimental.text.complete so it can scan "
            "outgoing assistant messages for stop patterns."
        )

    def test_plugin_registers_tool_execute_before(self):
        src = _src()
        assert "tool.execute.before" in src, (
            "Plugin must register tool.execute.before so it can DENY "
            "commit-shaped make targets while pending work exists."
        )

    def test_fail_open_pattern(self):
        src = _src()
        assert "catch" in src, (
            "Plugin hooks must fail open (try/catch returning output "
            "unchanged) -- never wedge the session on a plugin bug."
        )


class TestEnforcementDefaultIsOn:
    """Stop detection is state-based (TASKS.md, ratchet, gate).

    The rewrite (AS.1) collapsed 433 stop-signal patterns into 5 vocabulary
    patterns with state-based detection.
    """

    def test_state_based_detection_present(self):
        src = _src()
        assert "TASKS.md" in src, (
            "Plugin must read TASKS.md to detect unchecked items -- the "
            "state-based approach replaces env-var gating."
        )

    def test_stop_like_targets_defined(self):
        src = _src()
        assert "STOP_LIKE_TARGETS" in src, (
            "Plugin must define STOP_LIKE_TARGETS to recognize commit-shaped "
            "make targets."
        )


class TestResponseTransformDirective:
    """The text.complete hook detects stop patterns when work is pending."""

    def test_stop_detection_directive_present(self):
        src = _src()
        assert "HARD STOP" in src or "DELEGATE-FIRST" in src, (
            "Text completion hook must emit a delegation/resume directive "
            "when pending work is detected."
        )

    def test_summary_heuristic_present(self):
        src = _src()
        lower = src.lower()
        assert any(kw in lower for kw in ["stop", "pending", "work", "ratchet", "task"]), (
            "Stop detection must reference at least one of: stop, pending, "
            "work, ratchet, task."
        )

    def test_passes_through_when_no_pending_work(self):
        src = _src()
        assert "TASKS.md" in src, (
            "Plugin must read TASKS.md to determine if work is pending "
            "before blocking."
        )

    def test_response_passes_through_when_work_in_progress(self):
        src = _src()
        assert "make" in src.lower(), (
            "Plugin must recognize make invocations as work-in-progress "
            "(the agent is executing, not summarizing)."
        )

    def test_fails_open_on_error(self):
        src = _src()
        assert "try" in src.lower() and "catch" in src.lower(), (
            "State parsing must be wrapped in try/catch so errors do not "
            "wedge the session."
        )


class TestCommitBlock:
    """The tool.execute.before commit gate."""

    def test_commit_targets_recognized(self):
        """Commit-shaped make targets must be recognized and denied when
        pending work exists and is not addressed.
        """
        src = _src()
        for target in ["git-commit", "commit-no-verify", "ship-commit"]:
            assert target in src, (
                f"Commit block must recognize make {target} as a "
                "commit-shaped target. Missing it leaves a bypass."
            )

    def test_tasks_md_checked(self):
        src = _src()
        assert "TASKS.md" in src, (
            "Commit block must check TASKS.md for unchecked items."
        )

    def test_stop_like_targets_regex_present(self):
        src = _src()
        assert "STOP_LIKE_TARGETS" in src, (
            "Commit block must define STOP_LIKE_TARGETS to recognize "
            "commit-shaped make targets."
        )

    def test_deny_message_guides_agent(self):
        src = _src()
        lower = src.lower()
        assert any(kw in lower for kw in ["resume", "continue", "fix"]), (
            "Deny message must guide the agent to resume/continue/fix work."
        )


class TestProgressiveEscalation:
    """Progressive escalation: 1st block = warning, 2nd = stronger, 3rd+ = emergency."""

    def test_escalation_level_1_message(self):
        src = _src()
        assert "Fix pending work first, then retry." in src, (
            "Level 1 (first block): must show the base warning message."
        )

    def test_escalation_level_2_message(self):
        src = _src()
        assert "HARD STOP" in src, (
            "Level 2 (second consecutive block): must escalate to a hard stop message."
        )
        assert "DISPATCH SUBAGENTS NOW" in src, (
            "Level 2 warning must demand immediate subagent dispatch."
        )

    def test_escalation_level_3_emergency_override(self):
        src = _src()
        assert "EMERGENCY OVERRIDE" in src, (
            "Level 3+ (third+ consecutive block): must show EMERGENCY OVERRIDE."
        )
        assert "DISPATCH SUBAGENTS NOW" in src, (
            "Level 3+ must demand immediate subagent dispatch."
        )

    def test_force_dispatch_file_written_at_level_3(self):
        src = _src()
        assert "writeForceDispatch" in src, (
            "Level 3+ must call writeForceDispatch() to notify the watchdog."
        )
        assert "gludd-force-dispatch.json" in src, (
            "writeForceDispatch must write to /tmp/gludd-force-dispatch.json."
        )

    def test_blanked_response_tracker_exists(self):
        src = _src()
        assert "recordBlankedResponse" in src, (
            "Must call recordBlankedResponse() to track how many times the agent has been blanked."
        )
        assert "BlankedResponseTracker" in src or "totalBlanked" in src, (
            "Blanked response tracker must track totalBlanked count."
        )

    def test_consecutive_blocks_checked(self):
        src = _src()
        assert "consecutiveBlocks" in src, (
            "Escalation must be based on consecutiveBlocks from the block counter."
        )
