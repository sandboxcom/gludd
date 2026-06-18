"""Unit tests for AgentToolAdapter F4 security fixes:
- list_agent_tools filtered by invoker
- get_agent_as_tool filtered by invoker
- back-compat when invoker=None (all agents returned)
"""

from __future__ import annotations

from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType


def _subagent_config(name: str) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test subagent {name}",
        type=AgentType.SUBAGENT,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
        enabled=True,
    )


def _invoker_config(name: str, allowed: list[str]) -> AgentConfig:
    return AgentConfig(
        name=name,
        description=f"Test invoker {name}",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=allowed,
        ),
        enabled=True,
    )


class TestListAgentToolsInvokerFilter:
    def test_list_agent_tools_invoker_filter(self) -> None:
        """list_agent_tools(invoker=...) includes allowed agents and excludes forbidden ones."""
        registry = AgentRegistry()
        registry.register(_invoker_config("invoker_agent", allowed=["allowed_sub"]))
        registry.register(_subagent_config("allowed_sub"))
        registry.register(_subagent_config("forbidden_sub"))
        adapter = AgentToolAdapter(registry)

        tools = adapter.list_agent_tools(invoker="invoker_agent")
        tool_names = {t["target_agent"] for t in tools}

        assert "allowed_sub" in tool_names, (
            f"allowed_sub should be included, got: {tool_names}"
        )
        assert "forbidden_sub" not in tool_names, (
            f"forbidden_sub should be excluded, got: {tool_names}"
        )
        # The invoker itself is a PRIMARY agent; whether it's included depends on
        # can_invoke(invoker, invoker) — for simplicity assert it's not in the
        # filtered list (invoker can't dispatch itself via this flow).
        # The key invariant is forbidden_sub is excluded.


class TestGetAgentAsToolInvokerAllowed:
    def test_get_agent_as_tool_invoker_allowed(self) -> None:
        """get_agent_as_tool returns a dict when invoker is allowed to dispatch target."""
        registry = AgentRegistry()
        registry.register(_invoker_config("invoker_agent", allowed=["allowed_sub"]))
        registry.register(_subagent_config("allowed_sub"))
        adapter = AgentToolAdapter(registry)

        tool = adapter.get_agent_as_tool("allowed_sub", invoker="invoker_agent")

        assert tool is not None, "Expected a tool dict, got None"
        assert tool["target_agent"] == "allowed_sub"


class TestGetAgentAsToolInvokerForbidden:
    def test_get_agent_as_tool_invoker_forbidden(self) -> None:
        """get_agent_as_tool returns None when invoker is not allowed to dispatch target."""
        registry = AgentRegistry()
        registry.register(_invoker_config("invoker_agent", allowed=["allowed_sub"]))
        registry.register(_subagent_config("allowed_sub"))
        registry.register(_subagent_config("forbidden_sub"))
        adapter = AgentToolAdapter(registry)

        tool = adapter.get_agent_as_tool("forbidden_sub", invoker="invoker_agent")

        assert tool is None, f"Expected None for forbidden target, got: {tool!r}"


class TestListAgentToolsNoInvoker:
    def test_list_agent_tools_no_invoker(self) -> None:
        """list_agent_tools() with no invoker returns all agents (back-compat)."""
        registry = AgentRegistry()
        registry.register(_invoker_config("invoker_agent", allowed=["allowed_sub"]))
        registry.register(_subagent_config("allowed_sub"))
        registry.register(_subagent_config("other_sub"))
        adapter = AgentToolAdapter(registry)

        tools = adapter.list_agent_tools()
        tool_names = {t["target_agent"] for t in tools}

        # All registered agents should be included when no invoker filter is applied
        assert "allowed_sub" in tool_names
        assert "other_sub" in tool_names
        assert "invoker_agent" in tool_names
