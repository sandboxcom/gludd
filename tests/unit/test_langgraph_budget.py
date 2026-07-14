"""C.29 — LangGraph budget bypass: tool_auditor, budget_guard, adversarial_detector, max_total_tokens.

Tests that the LangGraphAgentLoop invokes the auditor on tool calls, blocks on
budget exhaustion, scans output with adversarial_detector, and enforces a
cumulative token cap.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
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


def _mock_mcp_client_with_tools(*tool_names: str) -> MagicMock:
    client = MagicMock()
    tools = []
    for name in tool_names:
        tool = MagicMock()
        tool.name = name
        tool.description = f"Tool: {name}"
        tool.input_schema = {"type": "object", "properties": {}}
        tools.append(tool)
    client.list_tools = AsyncMock(return_value=tools)
    client.call_tool = AsyncMock(return_value="tool-result")
    return client


def _mock_graph(final_content: str) -> MagicMock:
    from langchain_core.messages import AIMessage

    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                AIMessage(content=""),
                AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "1"}]),
                AIMessage(content="tool result"),
                AIMessage(content=final_content),
            ]
        }
    )
    return graph


def _make_graph_with_messages(messages: list) -> MagicMock:
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={"messages": messages})
    return graph


# ---------------------------------------------------------------------------
# budget_guard — blocks when guard denies
# ---------------------------------------------------------------------------


class TestBudgetGuard:
    def test_guard_denies_raises_runtime_error(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "budget exhausted"}
        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("final answer")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            budget_guard=guard,
        )

        with (
            patch("langgraph.prebuilt.create_react_agent", return_value=graph),
            pytest.raises(RuntimeError, match="budget exhausted"),
        ):
            asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))

        graph.ainvoke.assert_not_called()

    def test_guard_allows_proceeds(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": True}
        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("final answer")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            budget_guard=guard,
        )

        with patch("langgraph.prebuilt.create_react_agent", return_value=graph):
            result = asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))
        assert result == "final answer"

    def test_no_guard_proceeds_normally(self):
        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("ok")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
        )

        with patch("langgraph.prebuilt.create_react_agent", return_value=graph):
            result = asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))
        assert result == "ok"


# ---------------------------------------------------------------------------
# adversarial_detector — blocks output with adversarial content
# ---------------------------------------------------------------------------


class TestAdversarialDetector:
    def test_adversarial_content_blocks_output(self):
        detector = MagicMock()
        scan_result = MagicMock()
        scan_result.blocked = True
        scan_result.summary = "Adversarial detected: backdoor"
        detector.scan_text.return_value = scan_result

        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("evil content")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            adversarial_detector=detector,
        )

        with (
            patch("langgraph.prebuilt.create_react_agent", return_value=graph),
            pytest.raises(RuntimeError, match="blocked by adversarial"),
        ):
            asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))

        detector.scan_text.assert_called_once()

    def test_clean_output_proceeds(self):
        detector = MagicMock()
        scan_result = MagicMock()
        scan_result.blocked = False
        detector.scan_text.return_value = scan_result

        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("clean content")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            adversarial_detector=detector,
        )

        with patch("langgraph.prebuilt.create_react_agent", return_value=graph):
            result = asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))
        assert result == "clean content"

    def test_no_detector_proceeds(self):
        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("ok")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
        )

        with patch("langgraph.prebuilt.create_react_agent", return_value=graph):
            result = asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))
        assert result == "ok"


# ---------------------------------------------------------------------------
# tool_auditor — invoked during tool calls
# ---------------------------------------------------------------------------


class TestToolAuditor:
    def test_auditor_audit_called_for_tool_execution(self):
        client = _mock_mcp_client_with_tools("search")
        client.call_tool = AsyncMock(return_value="search-results")
        reg = _make_registry_with_tools("search")

        auditor = MagicMock()
        auditor.audit.return_value = None

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=MagicMock(),
            mcp_client=client,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        assert len(tools) == 1

        coro = tools[0].coroutine
        result = asyncio.run(coro(query="test"))
        assert result == "search-results"

        auditor.audit.assert_called_once()
        auditor.record_success.assert_called_once()
        auditor.record_error.assert_not_called()

    def test_auditor_record_error_on_timeout(self):
        client = _mock_mcp_client_with_tools("search")
        client.call_tool = AsyncMock(side_effect=TimeoutError())
        reg = _make_registry_with_tools("search")

        auditor = MagicMock()
        auditor.audit.return_value = None

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=MagicMock(),
            mcp_client=client,
            mcp_registry=reg,
            tool_auditor=auditor,
            per_tool_timeout=0.1,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        coro = tools[0].coroutine
        result = asyncio.run(coro(query="test"))
        assert "timed out" in result

        auditor.audit.assert_called_once()
        auditor.record_error.assert_called_once()
        auditor.record_success.assert_not_called()

    def test_auditor_record_error_on_exception(self):
        client = _mock_mcp_client_with_tools("search")
        client.call_tool = AsyncMock(side_effect=ValueError("bad args"))
        reg = _make_registry_with_tools("search")

        auditor = MagicMock()
        auditor.audit.return_value = None

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=MagicMock(),
            mcp_client=client,
            mcp_registry=reg,
            tool_auditor=auditor,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        coro = tools[0].coroutine
        result = asyncio.run(coro(query="test"))
        assert "Tool error" in result

        auditor.audit.assert_called_once()
        auditor.record_error.assert_called_once()
        auditor.record_success.assert_not_called()

    def test_no_auditor_tool_executes_normally(self):
        client = _mock_mcp_client_with_tools("search")
        client.call_tool = AsyncMock(return_value="results")
        reg = _make_registry_with_tools("search")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=MagicMock(),
            mcp_client=client,
            mcp_registry=reg,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        coro = tools[0].coroutine
        result = asyncio.run(coro(query="test"))
        assert result == "results"


# ---------------------------------------------------------------------------
# max_total_tokens — cumulative token cap
# ---------------------------------------------------------------------------


class TestMaxTotalTokens:
    def test_tokens_within_limit_proceeds(self):
        from langchain_core.messages import AIMessage

        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _make_graph_with_messages([
            AIMessage(
                content="ok",
                usage_metadata={"input_tokens": 50, "output_tokens": 25, "total_tokens": 75},
            ),
        ])
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            max_total_tokens=200,
        )

        with patch("langgraph.prebuilt.create_react_agent", return_value=graph):
            result = asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))
        assert result == "ok"

    def test_tokens_exceeded_raises(self):
        from langchain_core.messages import AIMessage

        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _make_graph_with_messages([
            AIMessage(
                content="final",
                usage_metadata={"input_tokens": 5000, "output_tokens": 100, "total_tokens": 5100},
            ),
        ])
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            max_total_tokens=100,
        )

        with (
            patch("langgraph.prebuilt.create_react_agent", return_value=graph),
            pytest.raises(RuntimeError, match="total tokens"),
        ):
            asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))

    def test_default_max_tokens_from_constant(self):
        from general_ludd.execution.langgraph_agent import MAX_TOTAL_TOKENS_DEFAULT

        assert MAX_TOTAL_TOKENS_DEFAULT == 100_000

    def test_multiple_messages_tokens_summed(self):
        from langchain_core.messages import AIMessage

        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _make_graph_with_messages([
            AIMessage(
                content="",
                usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
            ),
            AIMessage(
                content="",
                usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
            ),
            AIMessage(
                content="final",
                usage_metadata={"input_tokens": 40, "output_tokens": 15, "total_tokens": 55},
            ),
        ])
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            max_total_tokens=100,
        )

        with (
            patch("langgraph.prebuilt.create_react_agent", return_value=graph),
            pytest.raises(RuntimeError, match="total tokens"),
        ):
            asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))

    def test_messages_without_usage_metadata_handled(self):
        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("normal output")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            max_total_tokens=100,
        )

        with patch("langgraph.prebuilt.create_react_agent", return_value=graph):
            result = asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))
        assert result == "normal output"


# ---------------------------------------------------------------------------
# Integration — all guards active simultaneously
# ---------------------------------------------------------------------------


class TestAllGuardsActive:
    def test_budget_guard_checked_before_adversarial_and_tokens(self):
        guard = MagicMock()
        guard.check_all_limits.return_value = {"allowed": False, "reason": "exhausted"}
        detector = MagicMock()

        client = _mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = _mock_graph("content")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=client,
            mcp_registry=reg,
            budget_guard=guard,
            adversarial_detector=detector,
            max_total_tokens=50,
        )

        with (
            patch("langgraph.prebuilt.create_react_agent", return_value=graph),
            pytest.raises(RuntimeError, match="exhausted"),
        ):
            asyncio.run(loop.run_with_tools(_make_job(), "sys", "q"))

        detector.scan_text.assert_not_called()
        graph.ainvoke.assert_not_called()

    def test_all_guards_present_constructor(self):
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            budget_guard=object(),
            adversarial_detector=object(),
            tool_auditor=object(),
            max_total_tokens=5000,
        )
        assert loop._budget_guard is not None
        assert loop._adversarial_detector is not None
        assert loop._auditor is not None
        assert loop._max_total_tokens == 5000
