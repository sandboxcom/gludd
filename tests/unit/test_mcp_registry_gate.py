"""Tests for MCP registry collision guard and call_tool server-binding check.

FIX 1 -- tool-name collision: registering a tool whose name is already owned by a
         different server must raise ValueError, not silently overwrite.

FIX 2 -- dispatch gate bypass: MCPClient.call_tool must refuse to forward a
         tool_name that is not registered to the given server_id.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(server_id: str = "srv") -> MCPServerConfig:
    return MCPServerConfig(
        server_id=server_id,
        command=["python", "-m", "mcp_server"],
        args=[],
        env={},
    )


def _make_tool(name: str) -> MCPTool:
    return MCPTool(name=name, description="test tool")


def _make_mock_transport(tools: list[MCPTool] | None = None) -> MagicMock:
    t = MagicMock()
    t.start = AsyncMock()
    t.stop = AsyncMock()
    t.list_tools = AsyncMock(return_value=tools or [])
    t.call_tool = AsyncMock(return_value={"content": [{"type": "text", "text": "ok"}]})
    return t


# ---------------------------------------------------------------------------
# FIX 2 — call_tool server-binding gate
# ---------------------------------------------------------------------------

class TestCallToolDispatchGate:
    async def _client_with_tool(
        self, server_id: str, tool_name: str
    ) -> MCPClient:
        """Return a ready MCPClient that has `tool_name` registered to `server_id`."""
        configs = {server_id: _make_config(server_id)}
        registry = MCPToolRegistry()
        transport = _make_mock_transport(tools=[_make_tool(tool_name)])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", return_value=transport
        ):
            client = MCPClient(configs, registry)
            await client.start_all()

        return client

    async def test_call_tool_succeeds_for_registered_server(self) -> None:
        """A legitimately registered tool dispatches without error."""
        client = await self._client_with_tool("srv", "read_file")
        result = await client.call_tool("srv", "read_file", {"path": "/tmp/f"})
        assert result["content"][0]["text"] == "ok"

    async def test_call_tool_raises_for_unknown_tool_name(self) -> None:
        """A tool_name that was never registered must be refused."""
        client = await self._client_with_tool("srv", "real_tool")
        with pytest.raises(MCPTransportError, match="not registered"):
            await client.call_tool("srv", "ghost_tool", {})

    async def test_call_tool_raises_when_tool_belongs_to_different_server(self) -> None:
        """Forwarding a tool to a server that doesn't own it must be refused."""
        configs = {
            "srv-a": _make_config("srv-a"),
            "srv-b": _make_config("srv-b"),
        }
        registry = MCPToolRegistry()

        transport_a = _make_mock_transport(tools=[_make_tool("tool_a")])
        transport_b = _make_mock_transport(tools=[_make_tool("tool_b")])

        call_count = 0

        def _factory(config: MCPServerConfig, **_kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return transport_a if call_count == 1 else transport_b

        with patch("general_ludd.mcp.client.MCPStdioClient", side_effect=_factory):
            client = MCPClient(configs, registry)
            await client.start_all()

        # tool_a is owned by srv-a — asking srv-b to run it must be refused.
        with pytest.raises(MCPTransportError, match="not registered to server"):
            await client.call_tool("srv-b", "tool_a", {})

    async def test_call_tool_error_mentions_tool_and_server(self) -> None:
        """The error message must include both the tool name and the server id."""
        client = await self._client_with_tool("legitimate-srv", "real_tool")
        with pytest.raises(MCPTransportError) as exc_info:
            await client.call_tool("legitimate-srv", "ghost_tool", {})
        msg = str(exc_info.value)
        assert "ghost_tool" in msg
        assert "legitimate-srv" in msg

    async def test_call_tool_raises_when_no_transport(self) -> None:
        """Existing guard: unknown server_id still raises MCPTransportError."""
        configs: dict = {}
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)
        assert inspect.iscoroutinefunction(client.call_tool)

        with pytest.raises(MCPTransportError, match="No transport"):
            await client.call_tool("nonexistent", "any_tool", {})
