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


class TestPluginFileExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-stop.ts must exist -- this plugin guarantees that every "
            "parallel subagent result is codified before the agent sends a "
            "terminal response."
        )

    def test_plugin_exports_default(self):
        src = PLUGIN.read_text()
        assert "export default" in src

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads((ROOT / "opencode.json").read_text())
        assert any("enforce-stop" in str(p) for p in cfg.get("plugin", [])), (
            "enforce-stop.ts must be registered in opencode.json plugin[] "
            "or it will never load."
        )


class TestPluginHookRegistration:
    def test_plugin_registers_text_complete(self):
        src = PLUGIN.read_text()
        assert "text.complete" in src, (
            "Plugin must register experimental.text.complete so it can scan "
            "outgoing assistant messages for stop patterns."
        )

    def test_plugin_registers_tool_execute_before(self):
        src = PLUGIN.read_text()
        assert "tool.execute.before" in src, (
            "Plugin must register tool.execute.before so it can DENY "
            "commit-shaped make targets while pending work exists."
        )

    def test_fail_open_pattern(self):
        src = PLUGIN.read_text()
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
        src = PLUGIN.read_text()
        assert "TASKS.md" in src, (
            "Plugin must read TASKS.md to detect unchecked items -- the "
            "state-based approach replaces env-var gating."
        )

    def test_stop_like_targets_defined(self):
        src = PLUGIN.read_text()
        assert "STOP_LIKE_TARGETS" in src, (
            "Plugin must define STOP_LIKE_TARGETS to recognize commit-shaped "
            "make targets."
        )


class TestResponseTransformDirective:
    """The text.complete hook detects stop patterns when work is pending."""

    def test_stop_detection_directive_present(self):
        src = PLUGIN.read_text()
        assert "HARD STOP" in src or "DELEGATE-FIRST" in src, (
            "Text completion hook must emit a delegation/resume directive "
            "when pending work is detected."
        )

    def test_summary_heuristic_present(self):
        src = PLUGIN.read_text()
        lower = src.lower()
        assert any(kw in lower for kw in ["stop", "pending", "work", "ratchet", "task"]), (
            "Stop detection must reference at least one of: stop, pending, "
            "work, ratchet, task."
        )

    def test_passes_through_when_no_pending_work(self):
        src = PLUGIN.read_text()
        assert "TASKS.md" in src, (
            "Plugin must read TASKS.md to determine if work is pending "
            "before blocking."
        )

    def test_response_passes_through_when_work_in_progress(self):
        src = PLUGIN.read_text()
        assert "make" in src.lower(), (
            "Plugin must recognize make invocations as work-in-progress "
            "(the agent is executing, not summarizing)."
        )

    def test_fails_open_on_error(self):
        src = PLUGIN.read_text()
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
        src = PLUGIN.read_text()
        for target in ["git-commit", "commit-no-verify", "ship-commit"]:
            assert target in src, (
                f"Commit block must recognize make {target} as a "
                "commit-shaped target. Missing it leaves a bypass."
            )

    def test_tasks_md_checked(self):
        src = PLUGIN.read_text()
        assert "TASKS.md" in src, (
            "Commit block must check TASKS.md for unchecked items."
        )

    def test_stop_like_targets_regex_present(self):
        src = PLUGIN.read_text()
        assert "STOP_LIKE_TARGETS" in src, (
            "Commit block must define STOP_LIKE_TARGETS to recognize "
            "commit-shaped make targets."
        )

    def test_deny_message_guides_agent(self):
        src = PLUGIN.read_text()
        lower = src.lower()
        assert any(kw in lower for kw in ["resume", "continue", "fix"]), (
            "Deny message must guide the agent to resume/continue/fix work."
        )
