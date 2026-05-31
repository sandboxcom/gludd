"""Unit tests for MCP wiring into event loop and agent tool adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_harness.agents.registry import AgentRegistry, default_registry
from agentic_harness.agents.tool_adapter import AgentToolAdapter
from agentic_harness.event_loop.loop import EventLoop
from agentic_harness.mcp.registry import MCPTool, MCPToolRegistry


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class TestEventLoopMCPParameters:
    def test_event_loop_accepts_mcp_client_parameter(self):
        mcp_client = MagicMock()
        loop = EventLoop(mcp_client=mcp_client)
        assert loop._mcp_client is mcp_client

    def test_event_loop_accepts_mcp_tool_registry_parameter(self):
        registry = MCPToolRegistry()
        loop = EventLoop(mcp_tool_registry=registry)
        assert loop._mcp_tool_registry is registry

    def test_event_loop_mcp_defaults_to_none(self):
        loop = EventLoop()
        assert loop._mcp_client is None
        assert loop._mcp_tool_registry is None


class TestEventLoopGetAvailableTools:
    def test_get_available_tools_returns_empty_when_no_registry(self):
        loop = EventLoop()
        assert loop.get_available_tools() == []

    def test_get_available_tools_returns_mcp_tool_names(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))
        registry.register_tool("git", MCPTool(name="git_status", server_id="git"))
        loop = EventLoop(mcp_tool_registry=registry)
        tools = loop.get_available_tools()
        assert sorted(tools) == ["git_status", "read_file"]

    def test_get_available_tools_returns_empty_when_registry_empty(self):
        registry = MCPToolRegistry()
        loop = EventLoop(mcp_tool_registry=registry)
        assert loop.get_available_tools() == []


class TestEventLoopToolsInDispatchContext:
    @pytest.mark.asyncio
    async def test_dispatch_execute_includes_tools_in_budget_context(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))
        loop, mocks = _make_loop(mcp_tool_registry=registry)
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        todo.queue = "core"
        todo.work_type = "code"
        todo.resource_profile = "low_resource"
        todo.plan_artifact = None
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_args = mocks["http_client"].post.call_args
        body = call_args[1]["json"] if "json" in call_args[1] else call_args.kwargs["json"]
        assert "mcp_tools" in body["budget_context"]
        assert "read_file" in body["budget_context"]["mcp_tools"]

    @pytest.mark.asyncio
    async def test_dispatch_execute_no_tools_when_registry_absent(self):
        loop, mocks = _make_loop()
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        todo = MagicMock()
        todo.todo_id = "TODO-001"
        todo.queue = "core"
        todo.work_type = "code"
        todo.resource_profile = "low_resource"
        todo.plan_artifact = None
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_args = mocks["http_client"].post.call_args
        body = call_args[1]["json"] if "json" in call_args[1] else call_args.kwargs["json"]
        assert body["budget_context"].get("mcp_tools") is None or body["budget_context"].get("mcp_tools") == []


class TestAgentToolAdapter:
    def test_list_agent_tools_returns_all_agents(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools()
        names = {t["name"] for t in tools}
        assert names == {
            "dispatch_build",
            "dispatch_plan",
            "dispatch_explore",
            "dispatch_general",
        }

    def test_list_agent_tools_structure(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "target_agent" in tool
            assert "type" in tool
            assert tool["type"] == "agent_dispatch"
            assert tool["name"].startswith("dispatch_")

    def test_list_agent_tools_wraps_build_correctly(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools()
        build_tool = next(t for t in tools if t["target_agent"] == "build")
        assert build_tool["name"] == "dispatch_build"
        assert build_tool["description"] == "Primary build agent with all tool permissions"

    def test_list_agent_tools_wraps_plan_correctly(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools()
        plan_tool = next(t for t in tools if t["target_agent"] == "plan")
        assert plan_tool["name"] == "dispatch_plan"
        assert plan_tool["description"] == "Primary planning agent with read-only access"

    def test_list_agent_tools_wraps_explore_correctly(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools()
        explore_tool = next(t for t in tools if t["target_agent"] == "explore")
        assert explore_tool["name"] == "dispatch_explore"
        assert explore_tool["description"] == "Subagent for codebase exploration, read-only"

    def test_list_agent_tools_wraps_general_correctly(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tools = adapter.list_agent_tools()
        general_tool = next(t for t in tools if t["target_agent"] == "general")
        assert general_tool["name"] == "dispatch_general"
        assert general_tool["description"] == "General-purpose subagent with all tool permissions"

    def test_get_agent_as_tool_returns_specific_agent(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        tool = adapter.get_agent_as_tool("build")
        assert tool is not None
        assert tool["name"] == "dispatch_build"
        assert tool["target_agent"] == "build"
        assert tool["type"] == "agent_dispatch"

    def test_get_agent_as_tool_returns_none_for_unknown(self):
        registry = default_registry()
        adapter = AgentToolAdapter(registry)
        assert adapter.get_agent_as_tool("nonexistent") is None

    def test_list_agent_tools_empty_registry(self):
        registry = AgentRegistry()
        adapter = AgentToolAdapter(registry)
        assert adapter.list_agent_tools() == []
