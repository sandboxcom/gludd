"""Unit + functional tests for general_ludd.execution.tool_loop.

Targets:
  - ToolCallLoop.__init__
  - ToolCallLoop.is_available
  - ToolCallLoop._resolve_server_id
  - ToolCallLoop.run_with_tools
  - ToolCallLoop._call_model
  - ToolCallLoop._call_with_tools

Coverage goal: >85 % of tool_loop.py lines/branches.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.execution.tool_loop import (
    MAX_TOOL_ITERATIONS,
    PER_TOOL_TIMEOUT_SECONDS,
    ToolCallLoop,
)
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError
from general_ludd.schemas.job import JobSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(job_id: str = "job-1") -> JobSpec:
    return JobSpec(job_id=job_id, playbook="play.yml", queue="default")


def _make_registry(*tools: MCPTool) -> MCPToolRegistry:
    reg = MCPToolRegistry()
    for t in tools:
        reg.register_tool(t.server_id or "server-a", t)
    return reg


def _tool(name: str, server_id: str = "server-a") -> MCPTool:
    return MCPTool(name=name, description="desc", server_id=server_id)


def _simple_response(content: str = "done", tool_calls: list | None = None) -> MagicMock:
    """Build a mock gateway response object."""
    r = MagicMock()
    r.content = content
    r.tool_calls = tool_calls
    return r


def _tc(name: str, args: dict | str | None = None, tc_id: str = "tc-1") -> dict:
    """Build a tool_call dict as the model returns."""
    if args is None:
        args = {}
    return {
        "id": tc_id,
        "function": {
            "name": name,
            "arguments": args if isinstance(args, str) else json.dumps(args),
        },
    }


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_max_tool_iterations(self) -> None:
        assert MAX_TOOL_ITERATIONS == 10

    def test_per_tool_timeout(self) -> None:
        assert PER_TOOL_TIMEOUT_SECONDS == 30


# ---------------------------------------------------------------------------
# __init__ / is_available
# ---------------------------------------------------------------------------

class TestInit:
    def test_no_mcp_client(self) -> None:
        gw = MagicMock()
        loop = ToolCallLoop(gw)
        assert loop._gateway is gw
        assert loop._mcp_client is None
        assert loop._max_iterations == MAX_TOOL_ITERATIONS
        assert loop._per_tool_timeout == PER_TOOL_TIMEOUT_SECONDS
        assert loop._mcp_registry is None

    def test_with_mcp_client_and_no_registry_falls_back_to_facade_registry(self) -> None:
        gw = MagicMock()
        reg = _make_registry()
        client = MagicMock()
        client._registry = reg
        loop = ToolCallLoop(gw, mcp_client=client)
        assert loop._mcp_registry is reg

    def test_explicit_registry_wins_over_facade(self) -> None:
        gw = MagicMock()
        explicit_reg = _make_registry()
        facade_reg = _make_registry()
        client = MagicMock()
        client._registry = facade_reg
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=explicit_reg)
        assert loop._mcp_registry is explicit_reg

    def test_custom_iterations_and_timeout(self) -> None:
        gw = MagicMock()
        loop = ToolCallLoop(gw, max_iterations=5, per_tool_timeout=10.0)
        assert loop._max_iterations == 5
        assert loop._per_tool_timeout == 10.0

    def test_mcp_client_without_registry_attr_leaves_registry_none(self) -> None:
        gw = MagicMock(spec=[])
        client = MagicMock(spec=[])          # no _registry attribute
        loop = ToolCallLoop(gw, mcp_client=client)
        assert loop._mcp_registry is None

    def test_is_available_true_when_client_present(self) -> None:
        loop = ToolCallLoop(MagicMock(), mcp_client=MagicMock())
        assert loop.is_available() is True

    def test_is_available_false_when_no_client(self) -> None:
        loop = ToolCallLoop(MagicMock())
        assert loop.is_available() is False


# ---------------------------------------------------------------------------
# _resolve_server_id
# ---------------------------------------------------------------------------

class TestResolveServerId:
    def test_no_registry_raises(self) -> None:
        loop = ToolCallLoop(MagicMock())
        with pytest.raises(MCPTransportError, match="registry unavailable"):
            loop._resolve_server_id("any_tool")

    def test_unknown_tool_raises(self) -> None:
        reg = MCPToolRegistry()  # empty
        loop = ToolCallLoop(MagicMock(), mcp_registry=reg)
        with pytest.raises(MCPTransportError, match="not a registered MCP tool"):
            loop._resolve_server_id("phantom_tool")

    def test_ambiguous_tool_raises(self) -> None:
        """Same name registered on two servers → get_tool returns None but
        tool_names() contains it, so we should get the ambiguous error."""
        reg = MCPToolRegistry()
        # Registering the same name on two different servers causes a collision
        # *in the current implementation* (raises ValueError on second register).
        # The ambiguity path in _resolve_server_id covers the case where
        # get_tool returns None but tool_names() contains the name.
        # We patch get_tool and tool_names directly to exercise that branch.
        loop = ToolCallLoop(MagicMock(), mcp_registry=reg)
        with (
            patch.object(reg, "get_tool", return_value=None),
            patch.object(reg, "tool_names", return_value=["my_tool"]),
            pytest.raises(MCPTransportError, match="ambiguous"),
        ):
            loop._resolve_server_id("my_tool")

    def test_tool_with_empty_server_id_raises(self) -> None:
        reg = MCPToolRegistry()
        tool = MCPTool(name="broken_tool", server_id="srv")
        reg.register_tool("srv", tool)
        # Patch so tool.server_id appears empty after registry lookup
        tool_copy = MCPTool(name="broken_tool", server_id="")
        with patch.object(reg, "get_tool", return_value=tool_copy):
            loop = ToolCallLoop(MagicMock(), mcp_registry=reg)
            with pytest.raises(MCPTransportError, match="not a registered MCP tool"):
                loop._resolve_server_id("broken_tool")

    def test_known_tool_returns_server_id(self) -> None:
        reg = MCPToolRegistry()
        t = _tool("my_tool", server_id="srv-x")
        reg.register_tool("srv-x", t)
        loop = ToolCallLoop(MagicMock(), mcp_registry=reg)
        assert loop._resolve_server_id("my_tool") == "srv-x"


# ---------------------------------------------------------------------------
# run_with_tools — no MCP client (falls through to _call_model)
# ---------------------------------------------------------------------------

class TestRunWithToolsNoClient:
    @pytest.mark.asyncio
    async def test_no_mcp_returns_model_result(self) -> None:
        gw = MagicMock()
        gw.call_model.return_value = "model answer"
        loop = ToolCallLoop(gw)
        job = _make_job()
        result = await loop.run_with_tools(job, "sys", "user")
        assert result == "model answer"
        gw.call_model.assert_called_once_with(
            system_prompt="sys", user_prompt="user"
        )


# ---------------------------------------------------------------------------
# run_with_tools — happy path, single-shot (no tool calls)
# ---------------------------------------------------------------------------

class TestRunWithToolsHappyPath:
    def _make_loop(
        self, tools: list[MCPTool] | None = None
    ) -> tuple[ToolCallLoop, MagicMock, MagicMock]:
        gw = MagicMock()
        client = AsyncMock()
        mcp_tools = tools or [_tool("echo")]
        client.list_tools = AsyncMock(return_value=mcp_tools)
        reg = _make_registry(*mcp_tools)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)
        return loop, gw, client

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_content(self) -> None:
        loop, gw, _client = self._make_loop()
        job = _make_job()
        gw.call_model.return_value = _simple_response("final answer")
        result = await loop.run_with_tools(job, "sys", "user")
        assert result == "final answer"

    @pytest.mark.asyncio
    async def test_tool_schemas_built_from_list_tools(self) -> None:
        t = MCPTool(
            name="adder",
            description="adds",
            input_schema={"type": "object"},
            server_id="srv",
        )
        loop, gw, _client = self._make_loop(tools=[t])
        job = _make_job()
        gw.call_model.return_value = _simple_response("ok")
        await loop.run_with_tools(job, "sys", "user")
        call_kwargs = gw.call_model.call_args[1]
        schemas = call_kwargs["tools"]
        assert len(schemas) == 1
        assert schemas[0]["name"] == "adder"
        assert schemas[0]["description"] == "adds"
        assert schemas[0]["input_schema"] == {"type": "object"}

    @pytest.mark.asyncio
    async def test_response_content_attr_used(self) -> None:
        loop, gw, _client = self._make_loop()
        gw.call_model.return_value = _simple_response("hello")
        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_response_fallback_to_str_when_no_content_attr(self) -> None:
        loop, gw, _client = self._make_loop()
        # Return a bare string — no .content attribute
        gw.call_model.return_value = "bare string"
        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "bare string"


# ---------------------------------------------------------------------------
# run_with_tools — tool call cycle (single tool, result passback)
# ---------------------------------------------------------------------------

class TestToolExecutionCycle:
    def _make_loop_with_tool(
        self, tool_name: str = "my_tool"
    ) -> tuple[ToolCallLoop, MagicMock, MagicMock]:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool(tool_name, server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        client.call_tool = AsyncMock(return_value={"result": "42"})
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)
        return loop, gw, client

    @pytest.mark.asyncio
    async def test_single_tool_call_appends_result_and_continues(self) -> None:
        loop, gw, _client = self._make_loop_with_tool("my_tool")
        # First call: has tool_calls; second call: clean finish
        first_resp = _simple_response("...", tool_calls=[_tc("my_tool", {"x": 1})])
        second_resp = _simple_response("done")
        gw.call_model.side_effect = [first_resp, second_resp]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "done"
        assert gw.call_model.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_result_included_in_next_messages(self) -> None:
        loop, gw, client = self._make_loop_with_tool("my_tool")
        client.call_tool.return_value = "tool_output"
        first_resp = _simple_response(
            "...", tool_calls=[_tc("my_tool", {"k": "v"}, "id-99")]
        )
        second_resp = _simple_response("final")
        gw.call_model.side_effect = [first_resp, second_resp]

        await loop.run_with_tools(_make_job(), "s", "u")
        # Check second call's messages include the tool result
        second_call_messages = gw.call_model.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "id-99"
        assert "tool_output" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_tool_call_with_string_args_parsed_as_json(self) -> None:
        loop, gw, client = self._make_loop_with_tool("my_tool")
        string_args = json.dumps({"foo": "bar"})
        first_resp = _simple_response("...", tool_calls=[_tc("my_tool", string_args)])
        second_resp = _simple_response("ok")
        gw.call_model.side_effect = [first_resp, second_resp]

        await loop.run_with_tools(_make_job(), "s", "u")
        # call_tool should have received parsed dict
        call_args = client.call_tool.call_args
        assert call_args[0][2] == {"foo": "bar"}   # positional: server_id, name, args

    @pytest.mark.asyncio
    async def test_tool_call_with_invalid_json_args_falls_back_to_empty_dict(self) -> None:
        loop, gw, client = self._make_loop_with_tool("my_tool")
        first_resp = _simple_response("...", tool_calls=[_tc("my_tool", "NOT JSON {{{")])
        second_resp = _simple_response("ok")
        gw.call_model.side_effect = [first_resp, second_resp]

        await loop.run_with_tools(_make_job(), "s", "u")
        call_args = client.call_tool.call_args
        assert call_args[0][2] == {}

    @pytest.mark.asyncio
    async def test_tool_call_with_dict_args_passed_directly(self) -> None:
        loop, gw, client = self._make_loop_with_tool("my_tool")
        dict_args = {"already": "dict"}
        tc = {
            "id": "x",
            "function": {"name": "my_tool", "arguments": dict_args},
        }
        first_resp = MagicMock()
        first_resp.content = "..."
        first_resp.tool_calls = [tc]
        second_resp = _simple_response("ok")
        gw.call_model.side_effect = [first_resp, second_resp]

        await loop.run_with_tools(_make_job(), "s", "u")
        call_args = client.call_tool.call_args
        assert call_args[0][2] == {"already": "dict"}


# ---------------------------------------------------------------------------
# run_with_tools — timeout handling
# ---------------------------------------------------------------------------

class TestToolTimeout:
    def _make_loop(
        self, tool_name: str = "slow_tool", timeout: float = 1.0
    ) -> tuple[ToolCallLoop, MagicMock, MagicMock]:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool(tool_name, server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        reg = _make_registry(t)
        loop = ToolCallLoop(
            gw, mcp_client=client, per_tool_timeout=timeout, mcp_registry=reg
        )
        return loop, gw, client

    @pytest.mark.asyncio
    async def test_timeout_appends_error_message_and_continues(self) -> None:
        loop, gw, client = self._make_loop("slow_tool", timeout=0.01)

        async def _slow(*_a: Any, **_kw: Any) -> None:
            await asyncio.sleep(10)

        client.call_tool.side_effect = _slow

        first_resp = _simple_response("...", tool_calls=[_tc("slow_tool", {}, "t-id")])
        second_resp = _simple_response("recovered")
        gw.call_model.side_effect = [first_resp, second_resp]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "recovered"

        second_msgs = gw.call_model.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "timed out" in tool_msgs[0]["content"]
        assert "slow_tool" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_timeout_error_message_includes_timeout_value(self) -> None:
        loop, gw, client = self._make_loop("slow_tool", timeout=0.01)

        async def _slow(*_a: Any, **_kw: Any) -> None:
            await asyncio.sleep(10)

        client.call_tool.side_effect = _slow

        first_resp = _simple_response("...", tool_calls=[_tc("slow_tool")])
        second_resp = _simple_response("ok")
        gw.call_model.side_effect = [first_resp, second_resp]

        await loop.run_with_tools(_make_job(), "s", "u")
        second_msgs = gw.call_model.call_args_list[1][1]["messages"]
        tool_msg = next(m for m in second_msgs if m.get("role") == "tool")
        assert "0.01" in tool_msg["content"]


# ---------------------------------------------------------------------------
# run_with_tools — generic exception handling
# ---------------------------------------------------------------------------

class TestToolException:
    @pytest.mark.asyncio
    async def test_mcp_transport_error_appended_as_tool_error(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("bad_tool", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        client.call_tool = AsyncMock(side_effect=MCPTransportError("boom"))
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        first_resp = _simple_response("...", tool_calls=[_tc("bad_tool")])
        second_resp = _simple_response("ok")
        gw.call_model.side_effect = [first_resp, second_resp]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "ok"

        second_msgs = gw.call_model.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert "Tool error" in tool_msgs[0]["content"]
        assert "boom" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_capability_gate_error_appended_and_loop_continues(self) -> None:
        """A tool call for an unregistered name is rejected by _resolve_server_id
        and the error is surfaced as a tool-error message, not a crash."""
        gw = MagicMock()
        client = AsyncMock()
        # register nothing — all tool names are unknown
        client.list_tools = AsyncMock(return_value=[])
        reg = MCPToolRegistry()
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        first_resp = _simple_response("...", tool_calls=[_tc("phantom")])
        second_resp = _simple_response("recovered")
        gw.call_model.side_effect = [first_resp, second_resp]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "recovered"

        second_msgs = gw.call_model.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "Tool error" in tool_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_generic_exception_appended_as_tool_error(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("flaky", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        client.call_tool = AsyncMock(side_effect=RuntimeError("network gone"))
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        first_resp = _simple_response("...", tool_calls=[_tc("flaky")])
        second_resp = _simple_response("ok")
        gw.call_model.side_effect = [first_resp, second_resp]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "ok"
        second_msgs = gw.call_model.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert "network gone" in tool_msgs[0]["content"]


# ---------------------------------------------------------------------------
# run_with_tools — max iterations cap
# ---------------------------------------------------------------------------

class TestMaxIterations:
    @pytest.mark.asyncio
    async def test_loop_stops_at_max_iterations_returns_last_content(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("loopy", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        client.call_tool = AsyncMock(return_value="ok")
        reg = _make_registry(t)
        # max_iterations=3 → loop exits after 3 iterations even if model keeps
        # returning tool_calls
        loop = ToolCallLoop(gw, mcp_client=client, max_iterations=3, mcp_registry=reg)

        always_tool_resp = _simple_response("pending", tool_calls=[_tc("loopy")])
        gw.call_model.return_value = always_tool_resp

        result = await loop.run_with_tools(_make_job(), "s", "u")
        # Should return the last content seen, not raise
        assert result == "pending"
        assert gw.call_model.call_count == 3

    @pytest.mark.asyncio
    async def test_loop_exits_early_when_no_tool_calls(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("once", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        client.call_tool = AsyncMock(return_value="r")
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, max_iterations=10, mcp_registry=reg)

        first = _simple_response("inter", tool_calls=[_tc("once")])
        second = _simple_response("final")
        gw.call_model.side_effect = [first, second]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "final"
        assert gw.call_model.call_count == 2


# ---------------------------------------------------------------------------
# run_with_tools — multiple tool calls in one response
# ---------------------------------------------------------------------------

class TestMultipleToolCallsInOneResponse:
    @pytest.mark.asyncio
    async def test_multiple_tools_in_one_response_all_executed(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t1 = _tool("tool_a", server_id="srv")
        t2 = _tool("tool_b", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t1, t2])
        client.call_tool = AsyncMock(side_effect=["res_a", "res_b"])
        reg = MCPToolRegistry()
        reg.register_tool("srv", t1)
        reg.register_tool("srv", t2)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        first = _simple_response("...", tool_calls=[
            _tc("tool_a", {}, "id-a"),
            _tc("tool_b", {}, "id-b"),
        ])
        second = _simple_response("all done")
        gw.call_model.side_effect = [first, second]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "all done"
        assert client.call_tool.call_count == 2

        second_msgs = gw.call_model.call_args_list[1][1]["messages"]
        tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        ids = {m["tool_call_id"] for m in tool_msgs}
        assert ids == {"id-a", "id-b"}


# ---------------------------------------------------------------------------
# _call_model (direct)
# ---------------------------------------------------------------------------

class TestCallModel:
    @pytest.mark.asyncio
    async def test_call_model_delegates_to_gateway(self) -> None:
        gw = MagicMock()
        gw.call_model.return_value = "gateway_result"
        loop = ToolCallLoop(gw)
        result = await loop._call_model(_make_job(), "sys", "usr")
        gw.call_model.assert_called_once_with(system_prompt="sys", user_prompt="usr")
        assert result == "gateway_result"


# ---------------------------------------------------------------------------
# _call_with_tools (direct)
# ---------------------------------------------------------------------------

class TestCallWithTools:
    @pytest.mark.asyncio
    async def test_call_with_tools_passes_messages_and_schemas(self) -> None:
        gw = MagicMock()
        gw.call_model.return_value = "gw_resp"
        loop = ToolCallLoop(gw)
        msgs: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
        schemas: list[dict[str, Any]] = [
            {"name": "t", "description": "d", "input_schema": {}}
        ]
        result = await loop._call_with_tools(_make_job(), msgs, schemas)
        gw.call_model.assert_called_once_with(messages=msgs, tools=schemas)
        assert result == "gw_resp"


# ---------------------------------------------------------------------------
# Retry-on-transient-error: model gateway raises, then succeeds
# ---------------------------------------------------------------------------

class TestTransientRetry:
    """The tool loop itself doesn't retry gateway errors (the gateway/retry
    policy layer handles that), but we verify that a transient error in
    call_tool is properly surfaced as a tool-error message (so the model
    can recover) rather than propagating up and killing the loop."""

    @pytest.mark.asyncio
    async def test_transient_tool_error_surfaces_and_loop_recovers(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("retryable", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        # First call raises a transient error; loop should catch and continue
        client.call_tool = AsyncMock(side_effect=[
            ConnectionError("transient"),
            "success_result",
        ])
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        first = _simple_response("...", tool_calls=[_tc("retryable", {}, "id-1")])
        second = _simple_response("...", tool_calls=[_tc("retryable", {}, "id-2")])
        third = _simple_response("all good")
        gw.call_model.side_effect = [first, second, third]

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "all good"
        # First tool call failed (error msg), second succeeded
        assert client.call_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_no_tool_calls_attr_returns_content_immediately(self) -> None:
        """If response.tool_calls is None/falsy, return content without looping."""
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("x", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        resp = MagicMock()
        resp.content = "immediate"
        resp.tool_calls = None
        gw.call_model.return_value = resp

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "immediate"
        assert gw.call_model.call_count == 1

    @pytest.mark.asyncio
    async def test_empty_tool_calls_list_returns_content(self) -> None:
        gw = MagicMock()
        client = AsyncMock()
        t = _tool("x", server_id="srv")
        client.list_tools = AsyncMock(return_value=[t])
        reg = _make_registry(t)
        loop = ToolCallLoop(gw, mcp_client=client, mcp_registry=reg)

        resp = _simple_response("out", tool_calls=[])
        gw.call_model.return_value = resp

        result = await loop.run_with_tools(_make_job(), "s", "u")
        assert result == "out"
        assert gw.call_model.call_count == 1
