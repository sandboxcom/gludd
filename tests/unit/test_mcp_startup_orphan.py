from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


async def _build_mock_stdio_client(
    succeed: bool,
    server_id: str = "srv",
    tools: list[MCPTool] | None = None,
    error: Exception | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.start = AsyncMock()
    mock.list_tools = AsyncMock()
    mock.stop = AsyncMock()
    if succeed:
        if tools is None:
            tools = [MCPTool(name=f"tool_{server_id}", description="d", server_id=server_id)]
        mock.list_tools.return_value = tools
    else:
        exc = error or RuntimeError("MCP server failed to start")
        mock.start.side_effect = exc
    return mock


class TestMCPStartupOrphan:
    @pytest.mark.asyncio
    async def test_one_failure_kills_all_previously_started(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])
        cfg_b = MCPServerConfig(server_id="srv_b", command=["echo"])
        cfg_c = MCPServerConfig(server_id="srv_c", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = await _build_mock_stdio_client(succeed=True, server_id="a")
        transport_b = await _build_mock_stdio_client(succeed=True, server_id="b")
        transport_c = await _build_mock_stdio_client(succeed=False, server_id="c")

        mock_class = MagicMock(side_effect=[transport_a, transport_b, transport_c])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a, "b": cfg_b, "c": cfg_c},
                registry=registry,
            )
            with pytest.raises(RuntimeError):
                await client.start_all()

        transport_a.stop.assert_awaited()
        transport_b.stop.assert_awaited()
        transport_c.stop.assert_awaited()

        assert client._transports == {}

    @pytest.mark.asyncio
    async def test_all_succeed_normally(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])
        cfg_b = MCPServerConfig(server_id="srv_b", command=["echo"])
        cfg_c = MCPServerConfig(server_id="srv_c", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = await _build_mock_stdio_client(succeed=True, server_id="a")
        transport_b = await _build_mock_stdio_client(succeed=True, server_id="b")
        transport_c = await _build_mock_stdio_client(succeed=True, server_id="c")

        mock_class = MagicMock(side_effect=[transport_a, transport_b, transport_c])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a, "b": cfg_b, "c": cfg_c},
                registry=registry,
            )
            await client.start_all()

        transport_a.stop.assert_not_awaited()
        transport_b.stop.assert_not_awaited()
        transport_c.stop.assert_not_awaited()

        assert len(client._transports) == 3
        assert "a" in client._transports
        assert "b" in client._transports
        assert "c" in client._transports

        tools = await client.list_tools()
        assert len(tools) == 3

    @pytest.mark.asyncio
    async def test_cleanup_on_shutdown(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])
        cfg_b = MCPServerConfig(server_id="srv_b", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = await _build_mock_stdio_client(succeed=True, server_id="a")
        transport_b = await _build_mock_stdio_client(succeed=True, server_id="b")

        mock_class = MagicMock(side_effect=[transport_a, transport_b])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a, "b": cfg_b},
                registry=registry,
            )
            await client.start_all()

        transport_a.stop.assert_not_awaited()
        transport_b.stop.assert_not_awaited()

        await client.stop_all()

        transport_a.stop.assert_awaited()
        transport_b.stop.assert_awaited()
        assert client._transports == {}

    @pytest.mark.asyncio
    async def test_failure_with_no_previous_transports(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = await _build_mock_stdio_client(succeed=False, server_id="a")

        mock_class = MagicMock(side_effect=[transport_a])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a},
                registry=registry,
            )
            with pytest.raises(RuntimeError):
                await client.start_all()

        transport_a.stop.assert_awaited()
        assert client._transports == {}

    @pytest.mark.asyncio
    async def test_enabled_flag_respected(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"], enabled=True)
        cfg_b = MCPServerConfig(server_id="srv_b", command=["echo"], enabled=False)
        cfg_c = MCPServerConfig(server_id="srv_c", command=["echo"], enabled=True)

        registry = MCPToolRegistry()

        transport_a = await _build_mock_stdio_client(succeed=True, server_id="a")
        transport_c = await _build_mock_stdio_client(succeed=True, server_id="c")

        mock_class = MagicMock(side_effect=[transport_a, transport_c])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a, "b": cfg_b, "c": cfg_c},
                registry=registry,
            )
            await client.start_all()

        assert len(client._transports) == 2
        assert "b" not in client._transports

    @pytest.mark.asyncio
    async def test_failure_stops_previous_but_not_later(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])
        cfg_b = MCPServerConfig(server_id="srv_b", command=["echo"])
        cfg_c = MCPServerConfig(server_id="srv_c", command=["echo"])
        cfg_d = MCPServerConfig(server_id="srv_d", command=["echo"])

        registry = MCPToolRegistry()

        t_a = await _build_mock_stdio_client(succeed=True, server_id="a")
        t_b = await _build_mock_stdio_client(succeed=True, server_id="b")
        t_c = await _build_mock_stdio_client(succeed=False, server_id="c")
        t_d = await _build_mock_stdio_client(succeed=True, server_id="d")

        mock_class = MagicMock(side_effect=[t_a, t_b, t_c, t_d])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a, "b": cfg_b, "c": cfg_c, "d": cfg_d},
                registry=registry,
            )
            with pytest.raises(RuntimeError):
                await client.start_all()

        t_a.stop.assert_awaited()
        t_b.stop.assert_awaited()
        t_c.stop.assert_awaited()
        t_d.stop.assert_not_awaited()
        assert client._transports == {}
        assert client._started_pids == []

    @pytest.mark.asyncio
    async def test_pid_tracked_and_cleared_on_success(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = MagicMock()
        transport_a.start = AsyncMock()
        transport_a.list_tools = AsyncMock(
            return_value=[MCPTool(name="tool_a", description="d", server_id="a")]
        )
        transport_a.stop = AsyncMock()
        transport_a.pid = 12345

        mock_class = MagicMock(side_effect=[transport_a])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a},
                registry=registry,
            )
            await client.start_all()

        assert client._started_pids == [12345]
        assert "a" in client._transports

    @pytest.mark.asyncio
    async def test_pid_ignored_when_none(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = MagicMock()
        transport_a.start = AsyncMock()
        transport_a.list_tools = AsyncMock(
            return_value=[MCPTool(name="tool_a", description="d", server_id="a")]
        )
        transport_a.stop = AsyncMock()
        transport_a.pid = None

        mock_class = MagicMock(side_effect=[transport_a])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a},
                registry=registry,
            )
            await client.start_all()

        assert client._started_pids == []

    @pytest.mark.asyncio
    async def test_pids_cleared_on_failure(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])
        cfg_b = MCPServerConfig(server_id="srv_b", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = MagicMock()
        transport_a.start = AsyncMock()
        transport_a.list_tools = AsyncMock(
            return_value=[MCPTool(name="tool_a", description="d", server_id="a")]
        )
        transport_a.stop = AsyncMock()
        transport_a.pid = 11111

        transport_b = MagicMock()
        transport_b.start = AsyncMock(side_effect=RuntimeError("boom"))
        transport_b.stop = AsyncMock()
        transport_b.pid = 22222

        mock_class = MagicMock(side_effect=[transport_a, transport_b])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a, "b": cfg_b},
                registry=registry,
            )
            with pytest.raises(RuntimeError):
                await client.start_all()

        assert client._started_pids == []
        assert client._transports == {}

    @pytest.mark.asyncio
    async def test_pids_cleared_on_stop_all(self):
        cfg_a = MCPServerConfig(server_id="srv_a", command=["echo"])

        registry = MCPToolRegistry()

        transport_a = MagicMock()
        transport_a.start = AsyncMock()
        transport_a.list_tools = AsyncMock(
            return_value=[MCPTool(name="tool_a", description="d", server_id="a")]
        )
        transport_a.stop = AsyncMock()
        transport_a.pid = 33333

        mock_class = MagicMock(side_effect=[transport_a])

        with patch(
            "general_ludd.mcp.client.MCPStdioClient", mock_class
        ):
            client = MCPClient(
                configs={"a": cfg_a},
                registry=registry,
            )
            await client.start_all()

        assert client._started_pids == [33333]

        await client.stop_all()

        assert client._started_pids == []
        assert client._transports == {}
