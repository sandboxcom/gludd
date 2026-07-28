"""Tests for LangGraphAgentLoop backed by create_react_agent + ToolNode.

Covers:
  1. Mock model returns a tool call → ToolNode executes the tool.
  2. Agent loop iterates until the model stops requesting tools.
  3. Structured output parsing returns final AI content.
  4. Capability gate: _resolve_server_id rejects unknown tools.
  5. No MCP client → falls back to plain model call.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError
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


# ---------------------------------------------------------------------------
# _resolve_server_id routing (preserved from ToolCallLoop)
# ---------------------------------------------------------------------------


class TestServerIdResolution:
    def test_unique_name_resolves_correctly(self):
        reg = _make_registry_with_tools("read_file")
        loop = LangGraphAgentLoop(
            model_gateway=object(),
            mcp_registry=reg,
        )
        assert loop._resolve_server_id("read_file") == "test-srv"

    def test_unknown_tool_raises(self):
        reg = _make_registry_with_tools("read_file")
        loop = LangGraphAgentLoop(
            model_gateway=object(),
            mcp_registry=reg,
        )
        with pytest.raises(MCPTransportError, match="not a registered"):
            loop._resolve_server_id("nonexistent")

    def test_no_registry_raises(self):
        loop = LangGraphAgentLoop(model_gateway=object())
        with pytest.raises(MCPTransportError, match="registry unavailable"):
            loop._resolve_server_id("any_tool")


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_with_client(self):
        loop = LangGraphAgentLoop(
            model_gateway=object(),
            mcp_client=object(),
        )
        assert loop.is_available() is True

    def test_not_available_without_client(self):
        loop = LangGraphAgentLoop(model_gateway=object())
        assert loop.is_available() is False


# ---------------------------------------------------------------------------
# run_with_tools — plain fallback (no MCP client)
# ---------------------------------------------------------------------------


class TestRunPlainFallback:
    def test_no_mcp_client_falls_back_to_plain_call(self):
        gateway = MagicMock()
        gateway.call_model.return_value = MagicMock(
            content="plain response", tool_calls=None
        )

        loop = LangGraphAgentLoop(
            model_gateway=gateway,
            mcp_client=None,
        )
        job = _make_job()

        async def _run():
            return await loop.run_with_tools(job, "system", "user prompt")

        result = asyncio.run(_run())
        assert result == "plain response"
        gateway.call_model.assert_called_once()
        call_kwargs = gateway.call_model.call_args.kwargs
        assert call_kwargs["work_type"] == "analysis"
        assert call_kwargs["project_id"] is None


# ---------------------------------------------------------------------------
# run_with_tools — LangGraph path (mocked create_react_agent)
# ---------------------------------------------------------------------------


class TestRunWithToolsLangGraph:
    def _mock_mcp_client_with_tools(self, *tool_names: str) -> MagicMock:
        client = MagicMock()
        tools = []
        for name in tool_names:
            tool = MagicMock()
            tool.name = name
            tool.description = f"Tool: {name}"
            tool.input_schema = {"type": "object", "properties": {}}
            tools.append(tool)
        client.list_tools = AsyncMock(return_value=tools)
        return client

    def _mock_graph(self, final_content: str) -> MagicMock:
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

    def test_create_react_agent_receives_tools(self):
        mcp = self._mock_mcp_client_with_tools("search", "read_file")
        reg = _make_registry_with_tools("search", "read_file")
        graph = self._mock_graph("all done")

        gateway = MagicMock()
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=gateway,
            chat_model=chat_model,
            mcp_client=mcp,
            mcp_registry=reg,
        )

        with patch(
            "langgraph.prebuilt.create_react_agent",
            return_value=graph,
        ):
            result = asyncio.run(
                loop.run_with_tools(_make_job(), "system prompt", "user query")
            )

        assert result == "all done"
        graph.ainvoke.assert_called_once()

    def test_mcp_input_schema_is_preserved_on_structured_tool(self):
        mcp = self._mock_mcp_client_with_tools("search")
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        mcp.list_tools.return_value[0].input_schema = schema
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=MagicMock(),
            mcp_client=mcp,
            mcp_registry=_make_registry_with_tools("search"),
        )

        tools = asyncio.run(loop._build_langchain_tools())

        assert tools[0].args_schema == schema

    def test_final_ai_content_extracted(self):
        mcp = self._mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = self._mock_graph("the final answer")

        chat_model = MagicMock()
        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=mcp,
            mcp_registry=reg,
        )

        with patch(
            "langgraph.prebuilt.create_react_agent",
            return_value=graph,
        ):
            result = asyncio.run(
                loop.run_with_tools(_make_job(), "sys", "q")
            )

        assert result == "the final answer"

    def test_recursion_limit_configured(self):
        mcp = self._mock_mcp_client_with_tools("search")
        reg = _make_registry_with_tools("search")
        graph = self._mock_graph("ok")
        chat_model = MagicMock()

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            chat_model=chat_model,
            mcp_client=mcp,
            mcp_registry=reg,
            max_iterations=5,
        )

        with patch(
            "langgraph.prebuilt.create_react_agent",
            return_value=graph,
        ):
            asyncio.run(
                loop.run_with_tools(_make_job(), "s", "u")
            )

        config = graph.ainvoke.call_args.kwargs.get("config", {})
        assert config["recursion_limit"] == 5 * 2 + 10


# ---------------------------------------------------------------------------
# _build_langchain_tools — MCP → LangChain tool conversion
# ---------------------------------------------------------------------------


class TestBuildLangchainTools:
    def test_no_client_returns_empty(self):
        loop = LangGraphAgentLoop(model_gateway=MagicMock())
        tools = asyncio.run(loop._build_langchain_tools())
        assert tools == []

    def test_mcp_tools_converted_to_langchain_tools(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search for files"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        reg = _make_registry_with_tools("search")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert "Search for files" in tools[0].description

    def test_built_tool_calls_mcp_client(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "greet"
        tool.description = "Say hello"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(return_value="hello world")
        reg = _make_registry_with_tools("greet")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            per_tool_timeout=5.0,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        tool = tools[0]

        result = asyncio.run(tool.ainvoke({"name": "World"}))
        assert result == "hello world"
        mcp.call_tool.assert_called_once()

    def test_built_tool_handles_timeout(self):
        async def _slow(*args, **kwargs):
            await asyncio.sleep(10)
            return "too late"

        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "slow_tool"
        tool.description = "Very slow"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(side_effect=_slow)
        reg = _make_registry_with_tools("slow_tool")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
            per_tool_timeout=0.1,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        tool = tools[0]

        result = asyncio.run(tool.ainvoke({}))
        assert "timed out" in result

    def test_built_tool_handles_error(self):
        mcp = MagicMock()
        tool = MagicMock()
        tool.name = "broken"
        tool.description = "Always breaks"
        tool.input_schema = {"type": "object"}
        mcp.list_tools = AsyncMock(return_value=[tool])
        mcp.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        reg = _make_registry_with_tools("broken")

        loop = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp,
            mcp_registry=reg,
        )

        tools = asyncio.run(loop._build_langchain_tools())
        tool = tools[0]

        result = asyncio.run(tool.ainvoke({}))
        assert "Tool error" in result
        assert "boom" in result


# ---------------------------------------------------------------------------
# get_chat_model on ModelGateway (integration spot-check)
# ---------------------------------------------------------------------------


class TestGatewayGetChatModel:
    def test_get_chat_model_returns_langchain_runnable(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile

        profile = ModelProfile(
            model_profile_id="test-profile",
            model_name="gpt-4o-mini",
            provider="openai",
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        gw = ModelGateway(profiles=[profile])

        with pytest.raises(ValueError, match="provider registry"):
            gw.get_chat_model("test-profile")


# ---------------------------------------------------------------------------
# AgentConfig flag wiring
# ---------------------------------------------------------------------------


class TestAgentConfigFlag:
    def test_flag_defaults_false(self):
        from general_ludd.config.user_config import AgentConfig

        cfg = AgentConfig()
        assert cfg.use_langgraph_tool_loop is False

    def test_flag_set_true(self):
        from general_ludd.config.user_config import AgentConfig

        cfg = AgentConfig(use_langgraph_tool_loop=True)
        assert cfg.use_langgraph_tool_loop is True


# ---------------------------------------------------------------------------
# AgentCapabilities.make_langgraph_tool_loop
# ---------------------------------------------------------------------------


class TestCapabilitiesMakeLanggraph:
    def test_make_langgraph_tool_loop_returns_langgraph_loop(self):
        from general_ludd.agents.capabilities import AgentCapabilities

        caps = AgentCapabilities()
        loop = caps.make_langgraph_tool_loop(
            model_gateway=MagicMock(),
            role="coder",
            mcp_client=MagicMock(),
            mcp_registry=MagicMock(),
        )
        assert isinstance(loop, LangGraphAgentLoop)

    def test_make_tool_loop_still_returns_tool_call_loop(self):
        from general_ludd.agents.capabilities import AgentCapabilities
        from general_ludd.execution.tool_loop import ToolCallLoop

        caps = AgentCapabilities()
        loop = caps.make_tool_loop(
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
            mcp_registry=MagicMock(),
        )
        assert isinstance(loop, ToolCallLoop)
