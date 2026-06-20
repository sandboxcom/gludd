"""Unit tests for AgentToolAdapter.

Covers:
- list_agent_tools: invoker=None (no filter), invoker with permission, invoker unknown
- get_agent_as_tool: happy path, unknown agent, invoker denied
- dispatch_many([]) returns []
- _noop_executor default path (executor=None)
- can_invoke with allowed_subagents=[] always denies
"""

from __future__ import annotations

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher, AgentTask
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.tool_adapter import AgentToolAdapter
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry() -> AgentRegistry:
    """Return a registry with one PRIMARY invoker and two SUBAGENT targets."""
    reg = AgentRegistry()

    # Primary that can dispatch only "alpha"
    reg.register(AgentConfig(
        name="invoker",
        description="Primary invoker agent",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_dispatch_subagents=True,
            allowed_subagents=["alpha"],
        ),
    ))

    # Primary that cannot dispatch anything
    reg.register(AgentConfig(
        name="no_dispatch_invoker",
        description="No-dispatch primary",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_dispatch_subagents=False,
            allowed_subagents=[],
        ),
    ))

    reg.register(AgentConfig(
        name="alpha",
        description="Alpha subagent",
        type=AgentType.SUBAGENT,
    ))

    reg.register(AgentConfig(
        name="beta",
        description="Beta subagent",
        type=AgentType.SUBAGENT,
    ))

    return reg


def _adapter(reg: AgentRegistry | None = None) -> AgentToolAdapter:
    return AgentToolAdapter(reg or _make_registry())


# ---------------------------------------------------------------------------
# list_agent_tools
# ---------------------------------------------------------------------------

class TestListAgentTools:
    def test_invoker_none_returns_all_agents(self) -> None:
        """When invoker is None, all registered agents are listed."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tools = adapter.list_agent_tools(invoker=None)
        names = {t["target_agent"] for t in tools}
        assert names == {"invoker", "no_dispatch_invoker", "alpha", "beta"}

    def test_invoker_none_tool_shape(self) -> None:
        """Every tool dict contains the expected keys."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tools = adapter.list_agent_tools(invoker=None)
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "target_agent" in tool
            assert "type" in tool
            assert tool["type"] == "agent_dispatch"
            assert tool["name"].startswith("dispatch_")

    def test_invoker_permitted_filters_to_allowed(self) -> None:
        """Invoker 'invoker' can only dispatch 'alpha' — beta and primaries are filtered out."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tools = adapter.list_agent_tools(invoker="invoker")
        target_names = [t["target_agent"] for t in tools]
        assert target_names == ["alpha"]

    def test_invoker_no_dispatch_permission_returns_empty(self) -> None:
        """Invoker with can_dispatch_subagents=False sees an empty tool list."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tools = adapter.list_agent_tools(invoker="no_dispatch_invoker")
        assert tools == []

    def test_invoker_unknown_returns_empty(self) -> None:
        """An invoker not in the registry sees an empty tool list (can_invoke returns False)."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tools = adapter.list_agent_tools(invoker="nonexistent_invoker")
        assert tools == []


# ---------------------------------------------------------------------------
# get_agent_as_tool
# ---------------------------------------------------------------------------

class TestGetAgentAsTool:
    def test_happy_path_no_invoker(self) -> None:
        """Known agent with invoker=None returns a well-formed tool dict."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tool = adapter.get_agent_as_tool("alpha")
        assert tool is not None
        assert tool["target_agent"] == "alpha"
        assert tool["name"] == "dispatch_alpha"
        assert tool["type"] == "agent_dispatch"
        assert tool["description"] == "Alpha subagent"

    def test_unknown_agent_returns_none(self) -> None:
        """get_agent_as_tool returns None for an agent not in the registry."""
        reg = _make_registry()
        adapter = _adapter(reg)
        result = adapter.get_agent_as_tool("does_not_exist")
        assert result is None

    def test_invoker_permitted_returns_tool(self) -> None:
        """Permitted invoker→target returns the tool dict."""
        reg = _make_registry()
        adapter = _adapter(reg)
        tool = adapter.get_agent_as_tool("alpha", invoker="invoker")
        assert tool is not None
        assert tool["target_agent"] == "alpha"

    def test_invoker_denied_returns_none(self) -> None:
        """Invoker not allowed to dispatch 'beta' gets None."""
        reg = _make_registry()
        adapter = _adapter(reg)
        # 'invoker' only allows 'alpha', not 'beta'
        result = adapter.get_agent_as_tool("beta", invoker="invoker")
        assert result is None

    def test_invoker_no_dispatch_flag_returns_none(self) -> None:
        """Invoker with can_dispatch_subagents=False always gets None."""
        reg = _make_registry()
        adapter = _adapter(reg)
        result = adapter.get_agent_as_tool("alpha", invoker="no_dispatch_invoker")
        assert result is None

    def test_invoker_unknown_returns_none(self) -> None:
        """Unknown invoker causes can_invoke to fail → None returned."""
        reg = _make_registry()
        adapter = _adapter(reg)
        result = adapter.get_agent_as_tool("alpha", invoker="ghost")
        assert result is None


# ---------------------------------------------------------------------------
# dispatch_many([]) → []
# ---------------------------------------------------------------------------

class TestDispatchManyEmpty:
    @pytest.mark.asyncio()
    async def test_dispatch_many_empty_returns_empty_list(self) -> None:
        """dispatch_many with an empty task list returns [] without error."""
        reg = _make_registry()
        dispatcher = AgentDispatcher(registry=reg)
        results = await dispatcher.dispatch_many([])
        assert results == []


# ---------------------------------------------------------------------------
# _noop_executor default path
# ---------------------------------------------------------------------------

class TestNoopExecutor:
    @pytest.mark.asyncio()
    async def test_noop_executor_used_when_executor_is_none(self) -> None:
        """When executor=None the noop executor is used; dispatch succeeds with empty output."""
        reg = AgentRegistry()
        reg.register(AgentConfig(
            name="worker",
            description="Worker",
            type=AgentType.SUBAGENT,
        ))
        # No executor supplied → _noop_executor
        dispatcher = AgentDispatcher(registry=reg)
        task = AgentTask(
            task_id="noop1",
            agent_name="worker",
            description="noop run",
            prompt="do nothing",
        )
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"
        assert result.output == ""
        assert result.agent_name == "worker"

    @pytest.mark.asyncio()
    async def test_noop_executor_multiple_tasks(self) -> None:
        """Noop executor handles multiple tasks without error."""
        reg = AgentRegistry()
        reg.register(AgentConfig(
            name="worker",
            description="Worker",
            type=AgentType.SUBAGENT,
            max_concurrent=5,
        ))
        dispatcher = AgentDispatcher(registry=reg)
        tasks = [
            AgentTask(
                task_id=f"n{i}",
                agent_name="worker",
                description="",
                prompt="",
            )
            for i in range(3)
        ]
        results = await dispatcher.dispatch_many(tasks)
        assert len(results) == 3
        assert all(r.status == "completed" for r in results)
        assert all(r.output == "" for r in results)


# ---------------------------------------------------------------------------
# can_invoke with allowed_subagents=[]
# ---------------------------------------------------------------------------

class TestCanInvokeEmptyAllowedSubagents:
    def test_can_invoke_empty_allowed_subagents_always_false(self) -> None:
        """An invoker with can_dispatch_subagents=True but empty allowed_subagents cannot invoke any target."""
        from general_ludd.agents.registry import AgentRegistry

        reg = AgentRegistry()
        reg.register(AgentConfig(
            name="strict_invoker",
            description="Strict invoker",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=[],  # empty — no targets permitted
            ),
        ))
        reg.register(AgentConfig(
            name="target",
            description="Target",
            type=AgentType.SUBAGENT,
        ))

        # Even though can_dispatch_subagents=True, no target matches empty list
        assert reg.can_invoke("strict_invoker", "target") is False

    def test_can_invoke_empty_allowed_denies_wildcard_too(self) -> None:
        """An empty allowed_subagents list means even 'target' named '*' gets no match."""
        from general_ludd.agents.registry import AgentRegistry

        reg = AgentRegistry()
        reg.register(AgentConfig(
            name="strict",
            description="",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(
                can_dispatch_subagents=True,
                allowed_subagents=[],
            ),
        ))
        reg.register(AgentConfig(name="*", description="", type=AgentType.SUBAGENT))
        # fnmatch.fnmatch("*", pattern) for each pattern in [] → no matches
        assert reg.can_invoke("strict", "*") is False
