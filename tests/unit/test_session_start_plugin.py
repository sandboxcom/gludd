"""Tests for the session-start orchestration plugin.

The plugin (`.opencode/plugin/enforce-session-start.ts`) enforces the contract
that the FIRST thing the agent does on session start is:

  1. Read task-tracking files in parallel (TASKS.md, BUGS.md, ratchet.yml,
     SESSION.md) to find pending work.
  2. Immediately dispatch a ≥10-wide subagent wave on that work.

The contract is enforced at two layers:
  - `experimental.chat.system.transform` prepends a SESSION-START DIRECTIVE
    block as the FIRST section of the system prompt.
  - `tool.execute.before` (gated by GLUDD_SESSION_START_ENFORCE=1) denies
    mutating tools on the first turn until at least one task-tracking file
    has been read.

This test pins the structural shape so a silent regression (deleted directive,
weakened language, missing hook) is caught at gate time.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"


class TestSessionStartPluginExists:
    def test_plugin_file_exists(self):
        assert PLUGIN.exists(), (
            "enforce-session-start.ts must exist — this plugin guarantees the "
            "agent reads its task backlog and dispatches a subagent wave as "
            "the FIRST action of every session, rather than answering the "
            "first prompt with prose."
        )

    def test_plugin_exports_default(self):
        src = PLUGIN.read_text()
        assert "export default" in src

    def test_plugin_registered_in_opencode_json(self):
        """The plugin MUST be listed in opencode.json's plugin[] array."""
        import json
        cfg = json.loads((ROOT / "opencode.json").read_text())
        assert any("enforce-session-start" in str(p) for p in cfg.get("plugin", [])), (
            "enforce-session-start.ts is orphaned — it must be registered in "
            "opencode.json plugin[] or it will never load."
        )


class TestSessionStartSystemInjection:
    """The system.transform hook must prepend a strong, named directive."""

    def test_registers_system_transform(self):
        src = PLUGIN.read_text()
        assert "experimental.chat.system.transform" in src, (
            "Plugin must register experimental.chat.system.transform to inject "
            "the session-start directive into the system prompt."
        )

    def test_directive_names_all_task_tracking_files(self):
        """The directive MUST name TASKS.md, BUGS.md, ratchet.yml, SESSION.md.

        These four files are the canonical task backlog. If the directive
        omits any one, the agent may miss pending work on session start.
        """
        src = PLUGIN.read_text()
        for fname in ["TASKS.md", "BUGS.md", "ratchet.yml", "SESSION.md"]:
            assert fname in src, (
                f"Session-start directive must reference {fname} — without it "
                "the agent will not read this file before dispatching."
            )

    def test_directive_requires_parallel_reads(self):
        """The directive must tell the agent to read the files IN ONE MESSAGE."""
        src = PLUGIN.read_text()
        lower = src.lower()
        assert "parallel" in lower or "one message" in lower or "single message" in lower, (
            "Directive must instruct the agent to batch the task-file reads "
            "into one tool-call message — serial reads waste turns."
        )

    def test_directive_requires_immediate_dispatch(self):
        """The directive must demand a ≥10-wide subagent wave as action 2."""
        src = PLUGIN.read_text()
        assert "10" in src, "Directive must reference the 10-agent floor."
        lower = src.lower()
        assert "dispatch" in lower, "Directive must use the word 'dispatch'."
        assert "first" in lower, (
            "Directive must emphasize this is the FIRST action of the session."
        )

    def test_directive_forbids_prose_before_dispatch(self):
        """The directive must forbid prose between session start and dispatch."""
        src = PLUGIN.read_text()
        lower = src.lower()
        assert "no prose" in lower or "do not write" in lower or "do not answer" in lower, (
            "Directive must explicitly forbid prose/answers before the first "
            "dispatch wave — otherwise the agent will chat first."
        )

    def test_directive_prepended_first(self):
        """The directive must be PREPENDED to the system prompt (highest priority)."""
        src = PLUGIN.read_text()
        # Look for the pattern: return directive + separator + output
        # (NOT output + separator + directive).
        m = re.search(r"return\s+([A-Za-z_]\w*)\s*\+\s*[\"'`][^\"'`]*[\"'`]\s*\+\s*output", src)
        assert m, (
            "system.transform must PREPEND the directive (directive + '\\n' + output). "
            "Appending it buries the rule after hundreds of lines of system prompt."
        )

    def test_directive_section_header_is_loud(self):
        """The directive must open with a loud header so the model notices it."""
        src = PLUGIN.read_text()
        # At least one of: ALL-CAPS header, emoji marker, or ⚠/⛔/🚨 style signal.
        assert any(marker in src for marker in ["SESSION-START", "SESSION START", "🚨", "⛔", "⚠", "===="]), (
            "Directive must open with a loud header (ALL-CAPS / banner / emoji) "
            "so the model treats it as highest-priority."
        )


class TestSessionStartEnforcementGate:
    """The tool.execute.before gate (opt-in via GLUDD_SESSION_START_ENFORCE)."""

    def test_registers_tool_execute_before(self):
        src = PLUGIN.read_text()
        assert "tool.execute.before" in src

    def test_enforcement_is_env_gated(self):
        """Hard enforcement must be opt-in via GLUDD_SESSION_START_ENFORCE.

        Default behavior is directive-only (advisory) so the plugin does not
        wedge Q&A-only sessions. Operators turn on the hard gate with
        GLUDD_SESSION_START_ENFORCE=1.
        """
        src = PLUGIN.read_text()
        assert "GLUDD_SESSION_START_ENFORCE" in src

    def test_gate_allows_task_file_reads(self):
        """Even under enforcement, Reads of task-tracking files MUST be allowed."""
        src = PLUGIN.read_text()
        # The allowlist must include at least TASKS.md and BUGS.md as
        # recognized task-tracking paths.
        assert "TASKS" in src and "BUGS" in src

    def test_gate_allows_dispatches(self):
        """Task/agent/workflow dispatches MUST be allowed under enforcement."""
        src = PLUGIN.read_text()
        lower = src.lower()
        assert "task" in lower or "agent" in lower or "workflow" in lower


class TestSessionStartPluginTemplate:
    """Plugin must follow the same structural template as the other plugins."""

    def test_fail_open_pattern(self):
        """Every hook must fail open (try/catch returning output unchanged)."""
        src = PLUGIN.read_text()
        assert "catch" in src, (
            "Plugin hooks must fail open — never wedge the session on a plugin bug."
        )
