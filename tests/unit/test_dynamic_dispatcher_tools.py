"""Unit tests for dynamic-dispatcher tool binding.

Verifies that:
  1. When bind_tools_on_dispatch=True and an MCP tool registry is injected,
     dispatch_one populates task.tools with the schemas from list_tools().
  2. When bind_tools_on_dispatch=False, task.tools stays None.
  3. When no MCP registry is injected, task.tools stays None.
  4. The tool schemas have the correct shape (name, description, parameters).
  5. The executor receives the tools on the task object.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentConfig, AgentPermission, AgentTask, AgentType
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


def _make_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(AgentConfig(
        name="build",
        description="Primary agent",
        type=AgentType.PRIMARY,
        permissions=AgentPermission(
            can_dispatch_subagents=True,
            allowed_subagents=["*"],
        ),
        bind_tools_on_dispatch=True,
    ))
    reg.register(AgentConfig(
        name="worker",
        description="Worker subagent",
        type=AgentType.SUBAGENT,
        bind_tools_on_dispatch=True,
    ))
    reg.register(AgentConfig(
        name="worker_no_tools",
        description="Worker with tool binding disabled",
        type=AgentType.SUBAGENT,
        bind_tools_on_dispatch=False,
    ))
    return reg


def _make_tool_registry() -> MCPToolRegistry:
    reg = MCPToolRegistry()
    reg.register_tool("fs", MCPTool(
        name="read_file",
        description="Read a file from disk",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ))
    reg.register_tool("fs", MCPTool(
        name="write_file",
        description="Write content to a file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    ))
    return reg


async def _successful_executor(task: AgentTask) -> str:
    """Return a deterministic result while preserving the dispatched task."""
    return f"executed:{task.task_id}"


class _FailingToolRegistry:
    """Tool registry double that exposes a credential-bearing failure."""

    def list_tools(self) -> list[MCPTool]:
        """Raise the discovery failure exercised by the dispatcher boundary."""
        raise RuntimeError("token=dispatcher-secret")


class TestDispatcherToolBinding:
    def test_tools_populated_when_registry_injected(self) -> None:
        """dispatch_one populates task.tools when MCP registry is available."""
        agent_registry = _make_registry()
        tool_registry = _make_tool_registry()
        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_successful_executor,
            mcp_tool_registry=tool_registry,
        )

        task = AgentTask(
            task_id="t1",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is not None
        assert len(task.tools) == 2
        assert task.tools[0]["name"] == "read_file"
        assert task.tools[0]["description"] == "Read a file from disk"
        assert task.tools[0]["parameters"] == {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    def test_tools_not_populated_when_bind_disabled(self) -> None:
        """When bind_tools_on_dispatch=False, task.tools stays None."""
        agent_registry = _make_registry()
        tool_registry = _make_tool_registry()
        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_successful_executor,
            mcp_tool_registry=tool_registry,
        )

        task = AgentTask(
            task_id="t2",
            agent_name="worker_no_tools",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is None

    def test_tools_not_populated_when_no_registry(self) -> None:
        """When no MCP registry is injected, task.tools stays None."""
        agent_registry = _make_registry()
        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_successful_executor,
        )

        task = AgentTask(
            task_id="t3",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is None

    def test_tools_preserved_when_already_populated(self) -> None:
        """Existing task.tools are not overwritten by the dispatcher."""
        agent_registry = _make_registry()
        tool_registry = _make_tool_registry()
        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_successful_executor,
            mcp_tool_registry=tool_registry,
        )

        pre_populated_tools: list[dict[str, object]] = [
            {"name": "custom_tool", "description": "pre-set", "parameters": {}}
        ]
        task = AgentTask(
            task_id="t4",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
            tools=pre_populated_tools,
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is pre_populated_tools
        assert task.tools[0]["name"] == "custom_tool"

    def test_executor_receives_tools_on_task(self) -> None:
        """The executor function sees the populated tools on the task object."""
        agent_registry = _make_registry()
        tool_registry = _make_tool_registry()

        seen_tools: list[object] = []

        async def _capturing_executor(task: AgentTask) -> str:
            seen_tools.append(task.tools)
            return "ok"

        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_capturing_executor,
            mcp_tool_registry=tool_registry,
        )

        task = AgentTask(
            task_id="t5",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert len(seen_tools) == 1
        assert seen_tools[0] is not None
        assert len(cast(Any, seen_tools[0])) == 2
        assert cast(Any, seen_tools[0])[0]["name"] == "read_file"

    def test_empty_registry_produces_no_tools(self) -> None:
        """An MCP registry with no tools leaves task.tools as None."""
        agent_registry = _make_registry()
        empty_registry = MCPToolRegistry()
        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_successful_executor,
            mcp_tool_registry=empty_registry,
        )

        task = AgentTask(
            task_id="t6",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is None

    def test_bound_schema_is_detached_from_registry(self) -> None:
        """Executor-side schema mutation cannot alter the shared registry."""
        agent_registry = _make_registry()
        tool_registry = _make_tool_registry()
        dispatcher = AgentDispatcher(
            registry=agent_registry,
            executor=_successful_executor,
            mcp_tool_registry=tool_registry,
        )
        task = AgentTask(
            task_id="t7",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is not None
        parameters = cast(dict[str, Any], task.tools[0]["parameters"])
        path_schema = cast(dict[str, Any], parameters["properties"])["path"]
        cast(dict[str, Any], path_schema)["type"] = "integer"
        registered = tool_registry.get_tool("read_file")
        assert registered is not None
        assert registered.input_schema["properties"]["path"]["type"] == "string"

    def test_registry_failure_is_observable_and_sanitized(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Discovery failures are visible without leaking registry credentials."""
        dispatcher = AgentDispatcher(
            registry=_make_registry(),
            executor=_successful_executor,
            mcp_tool_registry=_FailingToolRegistry(),
        )
        task = AgentTask(
            task_id="t8",
            agent_name="worker",
            description="test",
            prompt="hello",
            invoker_name="build",
        )

        with caplog.at_level(logging.WARNING, logger="general_ludd.agents.dispatcher"):
            result = asyncio.run(dispatcher.dispatch_one(task))

        assert result.status == "completed"
        assert task.tools is None
        assert "tool discovery failed" in caplog.text.lower()
        assert "dispatcher-secret" not in caplog.text
        assert "[REDACTED_CREDENTIAL]" in caplog.text
