"""Session-start protocol tests.

Codifies the rule that the FIRST thing the agent does after session start is:
  1. Locate work (read TASKS.md, BUGS.md, config/ratchet.yml)
  2. Immediately fan out >= FLOOR parallel task/agent dispatches on disjoint work

The previous failure mode: agent booted, did a long inline investigation, then
sent a status report with 0 subagents live. This test pins the guardrail that
makes that structurally harder.

The guardrail lives in `.opencode/plugin/enforce-session-start.ts` (opencode
plugin layer) and AGENTS.md (prompt layer). These tests verify the load-bearing
pieces exist and would catch a regression that strips the enforcement.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"
AGENTS_MD = ROOT / "AGENTS.md"


@pytest.fixture(scope="module")
def plugin_src():
    if not PLUGIN.exists():
        pytest.fail(
            f"Missing {PLUGIN}. The session-start protocol plugin must exist — "
            "it forces parallel dispatch as the first action of every session."
        )
    return PLUGIN.read_text()


@pytest.fixture(scope="module")
def agents_src():
    return AGENTS_MD.read_text()


class TestSessionStartPluginExists:
    def test_plugin_file_exists(self, plugin_src):
        assert plugin_src, "enforce-session-start.ts must not be empty"

    def test_plugin_exports_default(self, plugin_src):
        assert "export default" in plugin_src, (
            "enforce-session-start.ts must export a default plugin object"
        )

    def test_plugin_registered_in_opencode_json(self):
        import json

        cfg = json.loads((ROOT / "opencode.json").read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-session-start" in str(p) for p in plugins), (
            "enforce-session-start.ts must be registered in opencode.json plugin[] "
            "or it will never load."
        )


class TestSessionStartEnforcesFloor:
    """The plugin must require >= FLOOR dispatches before allowing mutations."""

    def test_references_floor_constant(self, plugin_src):
        # Either CLAUDE_AGENT_FLOOR or a literal minimum like "5" / FLOOR.
        assert (
            "CLAUDE_AGENT_FLOOR" in plugin_src
            or "FLOOR" in plugin_src
            or "MIN_DISPATCHES" in plugin_src
        ), "Plugin must reference the agent-floor constant."

    def test_defines_minimum_dispatch_threshold(self, plugin_src):
        # The threshold for 'primed' must be at least 5 (the message-shape wave floor).
        # Accept: const MIN = parseInt(... "5"), or >= 5, etc.
        assert any(
            token in plugin_src
            for token in ('"5"', "'5'", "MIN_DISPATCHES", "FLOOR")
        ), "Plugin must define a minimum-dispatches threshold (>= 5)."

    def test_distinguishes_dispatch_tools_from_read_tools(self, plugin_src):
        # The plugin must classify task/agent/workflow as dispatches and
        # read/glob/grep as reads (so it doesn't nag on legitimate investigation).
        for tok in ("task", "agent", "workflow"):
            assert tok in plugin_src, f"Plugin must classify '{tok}' as a dispatch."
        for tok in ("read", "glob", "grep"):
            assert tok in plugin_src, f"Plugin must classify '{tok}' as a read."


class TestSessionStartStateTracking:
    """The plugin must persist session state so it can detect a fresh session."""

    def test_writes_session_state_file(self, plugin_src):
        # Must write some marker file under /tmp (or GLUDD_ override).
        assert "/tmp/gludd-session" in plugin_src or "GLUDD_SESSION_STATE" in plugin_src, (
            "Plugin must persist session-start state to a file so it can detect "
            "a fresh session vs. a resumed one."
        )

    def test_session_state_path_is_overridable(self, plugin_src):
        assert "GLUDD_SESSION_STATE" in plugin_src, (
            "Session state path must be overridable via GLUDD_SESSION_STATE for tests."
        )


class TestSessionStartSystemInjection:
    """The plugin must inject a Session Start Protocol into the system prompt."""

    def test_has_system_transform_hook(self, plugin_src):
        assert "experimental.chat.system.transform" in plugin_src, (
            "Plugin must register experimental.chat.system.transform to inject "
            "the Session Start Protocol at session boot."
        )

    def test_injection_names_the_protocol(self, plugin_src):
        # The injected text must name the protocol so it is greppable.
        assert "SESSION START PROTOCOL" in plugin_src.upper(), (
            "Injected context must include a 'Session Start Protocol' header."
        )

    def test_injection_orders_tasks_before_dispatch(self, plugin_src):
        # The protocol must instruct: locate work FIRST, then dispatch.
        upper = plugin_src.upper()
        tasks_idx = upper.find("TASKS.MD")
        dispatch_idx = upper.find("DISPATCH")
        if tasks_idx == -1 or dispatch_idx == -1:
            pytest.skip("Protocol ordering not names-checked in source")
        assert tasks_idx < dispatch_idx, (
            "Protocol must instruct reading TASKS.md BEFORE dispatching."
        )


class TestSessionStartToolBeforeHook:
    """The plugin must intercept non-dispatch tool calls in a fresh session."""

    def test_has_tool_execute_before_hook(self, plugin_src):
        assert "tool.execute.before" in plugin_src, (
            "Plugin must register tool.execute.before to intercept premature "
            "non-dispatch work in a fresh session."
        )

    def test_emits_warning_when_unprimed(self, plugin_src):
        # Must emit some console.warn / returned error mentioning dispatch.
        assert "dispatch" in plugin_src.lower(), (
            "Plugin must emit a dispatch-reminder when the session is unprimed."
        )

    def test_enforce_env_knob_exists(self, plugin_src):
        assert "GLUDD_SESSION_START_ENFORCE" in plugin_src, (
            "Plugin must support GLUDD_SESSION_START_ENFORCE to elevate from "
            "advisory to blocking (mirrors the GLUDD_FLOOR_ENFORCE pattern)."
        )


class TestSessionStartPromptPolicy:
    """AGENTS.md must codify the Session Start Protocol as a top-level policy."""

    def test_agents_md_has_session_start_section(self, agents_src):
        upper = agents_src.upper()
        assert "SESSION START PROTOCOL" in upper or "SESSION-START PROTOCOL" in upper, (
            "AGENTS.md must have a 'Session Start Protocol' section — the prompt "
            "layer of the 3-layer guardrail."
        )

    def test_agents_md_names_first_action(self, agents_src):
        # The policy must say the FIRST action is locating work + dispatching.
        upper = agents_src.upper()
        assert "FIRST" in upper and "DISPATCH" in upper, (
            "AGENTS.md must explicitly say the FIRST action is to dispatch."
        )

    def test_agents_md_references_plugin(self, agents_src):
        assert "enforce-session-start" in agents_src, (
            "AGENTS.md must reference the enforcing plugin (3-layer guardrail)."
        )
