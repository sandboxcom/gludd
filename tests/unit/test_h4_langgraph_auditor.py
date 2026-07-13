"""H.4 — LangGraph Agent Loop tool_auditor invocation tests.

The tool_auditor was stored in ``LangGraphAgentLoop._auditor`` but its audit()
verdict was never acted upon — calls proceeded even when the auditor said they
should be blocked. These tests prove the fix.

Covers:
  1. tool_auditor is invoked before each tool call in LangGraph agent loop
  2. tool_auditor can block tool calls that exceed budget/limits
  3. tool_auditor tracks cumulative tool usage
  4. Missing tool_auditor doesn't crash (graceful degradation)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
from general_ludd.execution.tool_auditor import (
    CallVerdict,
    ErrorLoopDetector,
    RedundancyDetector,
    ToolCallAuditor,
)
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.schemas.job import JobSpec


def _make_job(work_type: str = "analysis") -> JobSpec:
    return JobSpec(
        job_id="test-job-1",
        todo_id="todo-1",
        playbook="noop.yml",
        queue="core",
        work_type=work_type,
        model_profile="default",
    )


def _make_registry_with_tools(*names: str) -> MCPToolRegistry:
    reg = MCPToolRegistry()
    for name in names:
        reg.register_tool("test-srv", MCPTool(name=name, server_id="test-srv"))
    return reg


class TestLangGraphAuditorInvoked:
    """tool_auditor.audit() is called before each tool execution."""

    def test_auditor_called_before_mcp_tool(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = MagicMock(wraps=ToolCallAuditor())
        auditor.audit.return_value = CallVerdict(allowed=True)

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        result = asyncio.run(tools[0].ainvoke({}))

        auditor.audit.assert_called_once()
        call_args = auditor.audit.call_args
        assert call_args.args[0] == "search"
        assert call_args.kwargs.get("task_context") == "langgraph_agent"
        assert result == "result"

    def test_auditor_success_recorded_after_call(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = MagicMock(wraps=ToolCallAuditor())
        auditor.audit.return_value = CallVerdict(allowed=True)

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        asyncio.run(tools[0].ainvoke({}))

        auditor.record_success.assert_called_once()
        call_args = auditor.record_success.call_args
        assert call_args.args[0] == "search"
        assert call_args.args[2] == "result"

    def test_auditor_error_recorded_on_timeout(self):
        async def _slow(*args, **kwargs):
            await asyncio.sleep(10)
            return "too late"

        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "slow_tool"
        tool.description = "Slow"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(side_effect=_slow)
        reg = _make_registry_with_tools("slow_tool")

        auditor = MagicMock(wraps=ToolCallAuditor())
        auditor.audit.return_value = CallVerdict(allowed=True)

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
            per_tool_timeout=0.01,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        result = asyncio.run(tools[0].ainvoke({}))

        assert "timed out" in result
        auditor.record_error.assert_called_once()

    def test_auditor_error_recorded_on_exception(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "broken"
        tool.description = "Broken"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        reg = _make_registry_with_tools("broken")

        auditor = MagicMock(wraps=ToolCallAuditor())
        auditor.audit.return_value = CallVerdict(allowed=True)

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        result = asyncio.run(tools[0].ainvoke({}))

        assert "Tool error" in result
        assert "boom" in result
        auditor.record_error.assert_called_once()


class TestLangGraphAuditorBlocksCalls:
    """tool_auditor can block tool calls that exceed budget/limits."""

    def test_redundant_call_blocked_by_auditor(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=1),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())

        result1 = asyncio.run(tools[0].ainvoke({}))
        assert result1 == "result"

        result2 = asyncio.run(tools[0].ainvoke({}))
        assert "blocked" in result2.lower()
        assert "redundant" in result2.lower()
        assert "tool error" in result2.lower()

    def test_error_loop_call_blocked_by_auditor(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "fragile_tool"
        tool.description = "Fragile"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(side_effect=RuntimeError("broken"))
        reg = _make_registry_with_tools("fragile_tool")

        auditor = ToolCallAuditor(
            error_loop_detector=ErrorLoopDetector(max_error_retries=1),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())

        result1 = asyncio.run(tools[0].ainvoke({}))
        assert "Tool error" in result1

        result2 = asyncio.run(tools[0].ainvoke({}))
        assert "blocked" in result2.lower()

    def test_allowed_call_not_blocked(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=3),
            error_loop_detector=ErrorLoopDetector(max_error_retries=3),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        result = asyncio.run(tools[0].ainvoke({}))
        assert result == "result"


class TestLangGraphAuditorTracksUsage:
    """tool_auditor tracks cumulative tool usage via call_history."""

    def test_call_history_grows_with_tool_invocations(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=5),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())

        assert len(auditor.call_history) == 0
        asyncio.run(tools[0].ainvoke({"q": "first"}))
        assert len(auditor.call_history) >= 1
        asyncio.run(tools[0].ainvoke({"q": "second"}))
        assert len(auditor.call_history) >= 2

    def test_call_history_entries_contain_tool_name_and_args(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=5),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        asyncio.run(tools[0].ainvoke({"q": "hello"}))

        mcp.call_tool.assert_called_once()
        call_kwargs = mcp.call_tool.call_args
        assert call_kwargs.args[1] == "search"  # tool name

        entry = auditor.call_history[-1]
        assert entry["tool_name"] == "search"


class TestLangGraphNullAuditorGraceful:
    """Missing tool_auditor doesn't crash — graceful degradation."""

    def test_null_auditor_runs_tools_normally(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=None,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        result = asyncio.run(tools[0].ainvoke({}))
        assert result == "result"
        mcp.call_tool.assert_called_once()

    def test_no_auditor_by_default(self):
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
            mcp_registry=MagicMock(),
        )
        assert loop._auditor is None

    def test_multiple_tools_work_with_null_auditor(self):
        mcp = MagicMock()
        mcp_tools = []
        for name in ("search", "read_file"):
            t = MagicMock()
            t.name = name
            t.description = f"Tool: {name}"
            t.input_schema = {"type": "object"}
            mcp_tools.append(t)
        mcp.list_tools = AsyncMock(return_value=mcp_tools)
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search", "read_file")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=None,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        assert len(tools) == 2
        for tool in tools:
            result = asyncio.run(tool.ainvoke({}))
            assert result == "result"


class TestBlockedCallErrorMessage:
    """Blocked calls produce clear error messages that the model can understand."""

    def test_redundant_block_message_contains_reason(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=1),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        asyncio.run(tools[0].ainvoke({}))
        result = asyncio.run(tools[0].ainvoke({}))

        assert "redundant" in result.lower()
        assert "search" in result.lower()
        assert "do not retry" in result.lower()

    def test_blocked_call_does_not_reach_mcp(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="result")
        reg = _make_registry_with_tools("search")

        auditor = ToolCallAuditor(
            redundancy_detector=RedundancyDetector(max_repeats=1),
        )
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        asyncio.run(tools[0].ainvoke({}))
        mcp.call_tool.reset_mock()
        asyncio.run(tools[0].ainvoke({}))

        mcp.call_tool.assert_not_called()
