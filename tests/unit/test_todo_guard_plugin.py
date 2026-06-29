"""Tests for the nothing-dropped guardrail plugin.

The plugin (`.opencode/plugin/enforce-todos.ts`) prevents the recurring failure
mode where the agent dispatches N parallel subagents, receives N results, then
sends a text SUMMARY without codifying any of them — dropping the work.

Two enforcement layers:

  * `experimental.chat.response.transform` — when an active todowrite list has
    pending/in_progress items AND the outgoing response is a text summary with
    no tool call, PREPEND a loud directive telling the agent to resume work.
  * `tool.execute.before` (gated by `GLUDD_TODO_GUARD_ENFORCE`, default ON) —
    when a commit-shaped `make` target is invoked while pending todowrite items
    exist AND those items are not referenced in the commit message or in a
    staged TASKS.md update, DENY the commit.

TDD: this file was written FIRST and run RED against the missing plugin.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-todos.ts"


class TestPluginFileExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-todos.ts must exist — this plugin guarantees that every "
            "parallel subagent result is codified before the agent sends a "
            "terminal response. Without it the 'dispatch N -> summarize -> "
            "drop' failure mode recurs."
        )

    def test_plugin_exports_default(self):
        src = PLUGIN.read_text()
        assert "export default" in src

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads((ROOT / "opencode.json").read_text())
        assert any("enforce-todos" in str(p) for p in cfg.get("plugin", [])), (
            "enforce-todos.ts is orphaned — it must be registered in "
            "opencode.json plugin[] or it will never load."
        )


class TestPluginHookRegistration:
    def test_plugin_registers_response_transform(self):
        src = PLUGIN.read_text()
        assert "experimental.chat.response.transform" in src, (
            "Plugin must register experimental.chat.response.transform so it "
            "can scan outgoing assistant messages for the summary-without-"
            "codification pattern."
        )

    def test_plugin_registers_tool_execute_before(self):
        src = PLUGIN.read_text()
        assert "tool.execute.before" in src, (
            "Plugin must register tool.execute.before so it can DENY "
            "commit-shaped make targets while pending todowrite items exist."
        )

    def test_fail_open_pattern(self):
        src = PLUGIN.read_text()
        assert "catch" in src, (
            "Plugin hooks must fail open (try/catch returning output unchanged) "
            "— never wedge the session on a plugin bug."
        )


class TestEnforcementDefaultIsOn:
    """The hard commit gate must be ON by default (the `!== '0'` pattern).

    A default-OFF guardrail is advisory-only and will not stop the failure
    mode it was built for. The pattern `!== '0'` makes the gate active unless
    the operator explicitly sets GLUDD_TODO_GUARD_ENFORCE=0.
    """

    def test_enforcement_default_is_on(self):
        src = PLUGIN.read_text()
        # The canonical default-on pattern: process.env.X !== "0"
        assert re.search(
            r"GLUDD_TODO_GUARD_ENFORCE[^\n]*!==\s*\"0\"", src
        ), (
            "GLUDD_TODO_GUARD_ENFORCE must use the `!== \"0\"` default-on "
            "pattern, NOT `=== \"1\"`. A missing env var must default to ON."
        )

    def test_default_on_via_gludd_todo_guard_enforce(self):
        src = PLUGIN.read_text()
        # Negative assertion: the default-on gate must NOT use === "1".
        assert not re.search(
            r"GLUDD_TODO_GUARD_ENFORCE[^\n]*===\s*\"1\"", src
        ), (
            "GLUDD_TODO_GUARD_ENFORCE must not use `=== \"1\"` (opt-in). "
            "That makes the gate default-OFF. Use `!== \"0\"` instead."
        )


class TestResponseTransformDirective:
    """The response.transform hook must PREPEND a directive when:
      (a) the todo state file has pending/in_progress items, AND
      (b) the outgoing response looks like a summary (no tool call)."""

    def test_response_transform_prepends_directive_on_pending_todos_with_summary(self):
        src = PLUGIN.read_text()
        # The directive text must be present (the marker the test fixture
        # asserts against at runtime).
        assert "NOTHING-DROPPED GUARDRAIL" in src, (
            "Response transform must emit a NOTHING-DROPPED GUARDRAIL directive "
            "when pending todos exist and the response is a summary."
        )
        # The directive must reference 'pending' (the count of unfinished items).
        assert "pending" in src.lower()

    def test_response_transform_summary_heuristic_present(self):
        """The hook must implement a summary heuristic (keywords / bullet points
        + no make invocation). A naked pending-todos check would block every
        legitimate tool-bearing response."""
        src = PLUGIN.read_text()
        lower = src.lower()
        # At least one summary keyword must be referenced.
        assert any(kw in lower for kw in ["summary", "completed", "done", "results"]), (
            "Summary heuristic must reference at least one of: summary, "
            "completed, done, results."
        )

    def test_response_transform_passes_through_when_no_pending_todos(self):
        """When the todo state file has no pending items, the response must
        pass through unchanged. The plugin cannot block every terminal
        response — only those that drop pending work."""
        src = PLUGIN.read_text()
        # The hook must short-circuit when no pending items exist.
        # We look for a length/equality check on the pending-items list.
        assert re.search(r"pending\w*\.(length|size)|pending\w*\s*===\s*0|pending\w*\s*<\s*1|!pending\w*", src), (
            "Response transform must early-return when the pending-items list "
            "is empty (no pending work -> no directive)."
        )

    def test_response_transform_passes_through_when_response_has_tool_call(self):
        """When the response contains a tool call (e.g. references `make`), the
        directive must NOT fire — the agent is doing work, not summarizing."""
        src = PLUGIN.read_text()
        # The heuristic must exclude responses that contain a make invocation.
        assert "make" in src.lower(), (
            "Summary heuristic must exclude responses containing a `make` "
            "invocation (the agent is executing, not summarizing)."
        )

    def test_response_transform_fails_open_on_malformed_state(self):
        """A corrupt or unreadable todo state file must NOT crash the plugin —
        the response passes through unchanged. FAIL-OPEN is mandatory."""
        src = PLUGIN.read_text()
        # The JSON parse must be inside a try/catch that returns output.
        assert "try" in src.lower() and "catch" in src.lower(), (
            "Todo state parsing must be wrapped in try/catch so a corrupt "
            "file does not wedge the session."
        )
        # Must use JSON.parse (not a custom parser that could throw uncaught).
        assert "JSON.parse" in src


class TestCommitBlock:
    """The tool.execute.before commit gate."""

    def test_commit_block_fires_when_pending_todos_unrelated_to_commit(self):
        """When a commit-shaped make target runs while pending todos exist and
        are not addressed, the hook must DENY (permissionDecision: deny)."""
        src = PLUGIN.read_text()
        # Must reference permissionDecision: deny (the opencode deny shape).
        assert "permissionDecision" in src
        assert '"deny"' in src
        # Must enumerate the commit-shaped targets.
        for target in ["git-commit", "commit-no-verify", "repo-commit", "ship-commit"]:
            assert target in src, (
                f"Commit block must recognize `make {target}` as a commit-shaped "
                "target. Missing it leaves a bypass."
            )

    def test_commit_block_skipped_when_tasks_md_updated(self):
        """If the staged changes include a TASKS.md update, the commit is
        considered to have addressed the pending items — allow it."""
        src = PLUGIN.read_text()
        assert "TASKS.md" in src, (
            "Commit block must check staged changes for a TASKS.md update and "
            "treat it as addressing the pending items."
        )

    def test_commit_block_opt_out_via_env_var(self):
        """GLUDD_TODO_GUARD_BYPASS=1 must allow the commit through (emergency
        hotfix escape hatch). Documented but never the default."""
        src = PLUGIN.read_text()
        assert "GLUDD_TODO_GUARD_BYPASS" in src, (
            "Commit block must honor GLUDD_TODO_GUARD_BYPASS=1 as an emergency "
            "opt-out. Never the default."
        )

    def test_commit_block_message_references_pending_items(self):
        """The deny message must tell the agent to either complete, cancel-with-
        reason, or stage a TASKS.md update — so it knows how to proceed."""
        src = PLUGIN.read_text()
        lower = src.lower()
        assert "cancelled" in lower or "canceled" in lower, (
            "Deny message must mention cancelling pending items with a reason."
        )
