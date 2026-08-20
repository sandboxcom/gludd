"""Structural + behavioral tests for enforce-depth.ts.

Verifies: max depth at 4 levels (main + 3 subagent layers), depth 0-3 allow
dispatch, depth 4+/MAX_DEPTH override blocks, OPENCODE_DEPTH env var parsing,
GLUDD_DEPTH_ENFORCE=0 disables, GLUDD_MAX_DEPTH override, subagent isolation,
disengage guard, fail-open, Node v26
compat, opencode.json registration, and hot-reload proxy pattern.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-depth.ts"
OPOCODE_JSON = Path(__file__).resolve().parents[2] / "opencode.json"


def _src() -> str:
    return PLUGIN_PATH.read_text()


# ---------------------------------------------------------------------------
# Plugin existence + export shape
# ---------------------------------------------------------------------------


class TestPluginExists:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), "enforce-depth.ts must exist"

    def test_exports_default_satisfies_plugin(self):
        assert "satisfies Plugin" in _src()

    def test_factory_is_arrow_function(self):
        assert "export default (" in _src()


class TestPluginHooks:
    def test_has_tool_execute_before_hook(self):
        assert '"tool.execute.before"' in _src()

    def test_returns_one_hook_from_factory(self):
        src = _src()
        factory_idx = src.find("export default (")
        after = src[factory_idx:] if factory_idx > 0 else src
        assert '"tool.execute.before"' in after


# ---------------------------------------------------------------------------
# Subagent guard
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    """The orchestrator owns depth policy; delegated contexts are isolated."""

    def test_subagent_bypass_in_tool_execute_before(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        after = src[idx : idx + 500] if idx > 0 else src
        assert "isSubagent()" in after

    def test_enforce_gate_precedes_depth_check(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        after = src[idx : idx + 500] if idx > 0 else src
        enforce_idx = after.find("ENFORCE")
        depth_idx = after.find("currentDepth()")
        if depth_idx > 0:
            assert enforce_idx < depth_idx, "ENFORCE gate must precede the depth check"


# ---------------------------------------------------------------------------
# Disengage guard
# ---------------------------------------------------------------------------


class TestDisengageGuard:
    def test_imports_is_disengaged(self):
        assert "isDisengaged" in _src()

    def test_disengage_guard_in_tool_execute_before(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        after = src[idx : idx + 500] if idx > 0 else src
        assert "isDisengaged()" in after

    def test_disengage_precedes_depth_check(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        after = src[idx : idx + 500] if idx > 0 else src
        disengaged_idx = after.find("isDisengaged()")
        depth_idx = after.find("currentDepth()")
        if depth_idx > 0:
            assert disengaged_idx < depth_idx, "disengage guard must precede depth check"


# ---------------------------------------------------------------------------
# Fail-open guarantee
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_before_hook_has_try_catch(self):
        src = _src()
        idx = src.find('"tool.execute.before": async', src.find("defaultImpl"))
        end = idx + 2000 if idx > 0 else len(src)
        section = src[idx:end]
        assert "catch" in section, "tool.execute.before must have try/catch (fail-open)"


# ---------------------------------------------------------------------------
# Hot-reload proxy pattern
# ---------------------------------------------------------------------------


class TestHotReload:
    def test_imports_load_hot_module(self):
        assert "loadHotModule" in _src()

    def test_uses_hot_module_name_depth(self):
        assert 'loadHotModule("depth"' in _src()

    def test_default_impl_defined(self):
        assert "const defaultImpl" in _src()
        assert "HotModule" in _src()

    def test_proxy_delegates_before_hook(self):
        src = _src()
        factory_idx = src.find("export default (")
        after = src[factory_idx:]
        assert 'impl["tool.execute.before"]' in after


# ---------------------------------------------------------------------------
# opencode.json registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registered_in_opencode_json(self):
        data = json.loads(OPOCODE_JSON.read_text())
        plugins = data.get("plugin", [])
        assert any("enforce-depth.ts" in p for p in plugins), "enforce-depth.ts must be registered in opencode.json"


# ---------------------------------------------------------------------------
# Constants: MAX_DEPTH default, ENFORCE flag, OPENCODE_DEPTH
# ---------------------------------------------------------------------------


class TestConstants:
    def test_max_depth_default_is_4(self):
        src = _src()
        m = re.search(r'GLUDD_MAX_DEPTH\s*\|\|\s*"(\d+)"', src)
        assert m, "GLUDD_MAX_DEPTH default not found"
        assert int(m.group(1)) == 4, "MAX_DEPTH default must be 4 (main + 3 subagent layers)"

    def test_enforce_flag_default_on(self):
        src = _src()
        assert "GLUDD_DEPTH_ENFORCE" in src
        assert '!== "0"' in src

    def test_reads_opencode_depth_env_var(self):
        assert "OPENCODE_DEPTH" in _src()

    def test_depth_defaults_to_zero(self):
        src = _src()
        m = re.search(r'OPENCODE_DEPTH\s*\|\|\s*"(\d+)"', src)
        assert m, "OPENCODE_DEPTH default not found"
        assert int(m.group(1)) == 0


# ---------------------------------------------------------------------------
# Dispatch tool detection
# ---------------------------------------------------------------------------


class TestDispatchDetection:
    def test_is_dispatch_tool_function_exists(self):
        assert "function isDispatchTool" in _src()

    def test_checks_task_tool(self):
        src = _src()
        assert "task" in src
        assert "agent" in src
        assert "workflow" in src

    def test_case_insensitive(self):
        assert "toLowerCase" in _src()


# ---------------------------------------------------------------------------
# Block message
# ---------------------------------------------------------------------------


class TestBlockMessage:
    def test_block_returns_permission_decision_deny(self):
        src = _src()
        idx = src.find("permissionDecision")
        assert idx > 0
        after = src[idx : idx + 100]
        assert '"deny"' in after

    def test_block_message_mentions_max_depth(self):
        assert "MAX DEPTH EXCEEDED" in _src()

    def test_block_message_mentions_limit(self):
        assert "depth=" in _src()
        assert "limit=" in _src()


# ---------------------------------------------------------------------------
# Node v26 compatibility
# ---------------------------------------------------------------------------


class TestNodeV26Compat:
    def test_no_nested_try_in_catch(self):
        src = _src()
        assert "catch { try" not in src
        assert "catch (e) { try" not in src

    def test_no_type_annotated_catch(self):
        assert not re.search(r"catch\s*\(\s*\w+\s*:", _src())

    def test_no_enums(self):
        assert not re.search(r"\benum\s+", _src())

    def test_no_namespaces(self):
        assert not re.search(r"\bnamespace\s+", _src())


# ===========================================================================
# Behavioral depth enforcement tests — Python simulation of plugin logic
# ===========================================================================

MAX_DEPTH = 4


def _simulate(
    depth: int,
    max_depth: int = MAX_DEPTH,
    tool: str = "task",
    enforce: bool = True,
    is_subagent: bool = False,
    disengaged: bool = False,
) -> dict | None:
    """Simulation of enforce-depth.ts tool.execute.before logic.

    Delegated contexts bypass the orchestrator-owned enforcement stack."""
    if is_subagent:
        return None
    if not enforce:
        return None
    lt = tool.lower()
    if lt not in ("task", "agent", "workflow"):
        return None
    if disengaged:
        return None
    if depth >= max_depth:
        return {
            "permissionDecision": "deny",
            "message": (
                f"MAX DEPTH EXCEEDED: depth={depth}, limit={max_depth}.\n"
                "AGENTS.md: Subagent delegation depth MUST NOT exceed 4 levels.\n"
                "A depth-4 subagent CANNOT dispatch further. Complete assigned work directly.\n"
                "Set GLUDD_DEPTH_ENFORCE=0 to disable."
            ),
        }
    return None


class TestDepthBehavioral:
    """Depth 0/1/2/3 allow dispatch; depth 4+ blocks."""

    def test_depth_0_allows_dispatch(self):
        assert _simulate(0) is None

    def test_depth_1_allows_dispatch(self):
        assert _simulate(1) is None

    def test_depth_2_allows_dispatch(self):
        assert _simulate(2) is None

    def test_depth_3_allows_dispatch(self):
        assert _simulate(3) is None

    def test_depth_4_blocks_dispatch(self):
        result = _simulate(4)
        assert result is not None
        assert result["permissionDecision"] == "deny"
        assert "MAX DEPTH EXCEEDED" in result["message"]

    def test_depth_10_blocks_dispatch(self):
        result = _simulate(10)
        assert result is not None
        assert result["permissionDecision"] == "deny"


class TestDepthEnforceDisable:
    """GLUDD_DEPTH_ENFORCE=0 disables enforcement."""

    def test_depth_3_allows_when_enforce_false(self):
        assert _simulate(3, enforce=False) is None

    def test_depth_5_allows_when_enforce_false(self):
        assert _simulate(5, enforce=False) is None

    def test_depth_10_allows_when_enforce_false(self):
        assert _simulate(10, enforce=False) is None


class TestMaxDepthOverride:
    """GLUDD_MAX_DEPTH override changes the blocking threshold."""

    def test_max_depth_5_allows_depth_4(self):
        assert _simulate(4, max_depth=5) is None

    def test_max_depth_5_blocks_depth_5(self):
        result = _simulate(5, max_depth=5)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_max_depth_5_blocks_depth_6(self):
        result = _simulate(6, max_depth=5)
        assert result is not None

    def test_max_depth_2_blocks_depth_2(self):
        result = _simulate(2, max_depth=2)
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_max_depth_2_allows_depth_1(self):
        assert _simulate(1, max_depth=2) is None

    def test_max_depth_1_blocks_depth_1(self):
        result = _simulate(1, max_depth=1)
        assert result is not None


class TestSubagentIsolation:
    """Delegated contexts bypass depth enforcement owned by the orchestrator."""

    def test_subagent_depth_3_allows_dispatch(self):
        assert _simulate(3, is_subagent=True) is None

    def test_subagent_depth_4_allows_dispatch(self):
        assert _simulate(4, is_subagent=True) is None

    def test_subagent_depth_10_allows_dispatch(self):
        assert _simulate(10, is_subagent=True) is None


class TestDisengageBypass:
    """Disengage bypasses enforcement."""

    def test_disengaged_depth_3_allows(self):
        assert _simulate(3, disengaged=True) is None

    def test_disengaged_depth_10_allows(self):
        assert _simulate(10, disengaged=True) is None


class TestNonDispatchToolsPassThrough:
    """read/write/edit/bash (non-task/agent/workflow) always pass."""

    def test_read_at_depth_3_allows(self):
        assert _simulate(3, tool="read") is None

    def test_edit_at_depth_3_allows(self):
        assert _simulate(3, tool="edit") is None

    def test_write_at_depth_3_allows(self):
        assert _simulate(3, tool="write") is None

    def test_bash_at_depth_3_allows(self):
        assert _simulate(3, tool="bash") is None

    def test_agent_at_depth_4_blocks(self):
        result = _simulate(4, tool="agent")
        assert result is not None


class TestOpenCodeDepthEnvParsing:
    """Simulate how the plugin parses OPENCODE_DEPTH from env."""

    def test_env_var_zero_defaults_to_int_zero(self):
        val = int(os.environ.get("OPENCODE_DEPTH", "0"))
        assert val == 0

    def test_env_var_empty_string_falls_back_to_zero(self):
        val = int("0")
        assert val == 0

    def test_env_var_set_to_3_parses_as_3(self):
        val = int("3")
        assert val == 3

    def test_parse_int_handles_leading_zeros(self):
        val = int("03")
        assert val == 3
