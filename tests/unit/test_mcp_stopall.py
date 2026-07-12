"""Unit tests for H.9 — MCP stop_all orphan fix."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError


@pytest.mark.asyncio
async def test_stop_all_one_failure_still_stops_others():
    """One transport.stop() raising still stops the rest and clears the map."""
    registry = MCPToolRegistry()
    configs = {
        "srv_a": MCPServerConfig(server_id="srv_a", command=["echo", "a"]),
        "srv_b": MCPServerConfig(server_id="srv_b", command=["echo", "b"]),
        "srv_c": MCPServerConfig(server_id="srv_c", command=["echo", "c"]),
    }
    client = MCPClient(configs=configs, registry=registry)

    mock_a = MagicMock()
    mock_a.stop = AsyncMock(side_effect=RuntimeError("boom"))
    mock_b = MagicMock()
    mock_b.stop = AsyncMock()
    mock_c = MagicMock()
    mock_c.stop = AsyncMock()

    client._transports["srv_a"] = mock_a
    client._transports["srv_b"] = mock_b
    client._transports["srv_c"] = mock_c
    client._started_pids = [1, 2, 3]

    with pytest.raises(MCPTransportError, match="srv_a"):
        await client.stop_all()

    mock_a.stop.assert_awaited_once()
    mock_b.stop.assert_awaited_once()
    mock_c.stop.assert_awaited_once()

    assert len(client._transports) == 0
    assert len(client._started_pids) == 0


@pytest.mark.asyncio
async def test_stop_all_multiple_failures_reports_all():
    """Multiple failing transports all get reported in the error message."""
    registry = MCPToolRegistry()
    configs = {
        "srv_a": MCPServerConfig(server_id="srv_a", command=["echo", "a"]),
        "srv_b": MCPServerConfig(server_id="srv_b", command=["echo", "b"]),
        "srv_c": MCPServerConfig(server_id="srv_c", command=["echo", "c"]),
    }
    client = MCPClient(configs=configs, registry=registry)

    mock_a = MagicMock()
    mock_a.stop = AsyncMock(side_effect=RuntimeError("boom_a"))
    mock_b = MagicMock()
    mock_b.stop = AsyncMock(side_effect=OSError("boom_b"))
    mock_c = MagicMock()
    mock_c.stop = AsyncMock()

    client._transports["srv_a"] = mock_a
    client._transports["srv_b"] = mock_b
    client._transports["srv_c"] = mock_c

    with pytest.raises(MCPTransportError) as exc_info:
        await client.stop_all()

    msg = str(exc_info.value)
    assert "2 transport" in msg
    assert "srv_a" in msg
    assert "srv_b" in msg

    mock_c.stop.assert_awaited_once()
    assert len(client._transports) == 0


@pytest.mark.asyncio
async def test_stop_all_all_succeed_no_error():
    """When all transports stop cleanly, no error is raised."""
    registry = MCPToolRegistry()
    configs = {
        "srv_a": MCPServerConfig(server_id="srv_a", command=["echo", "a"]),
    }
    client = MCPClient(configs=configs, registry=registry)

    mock = MagicMock()
    mock.stop = AsyncMock()
    client._transports["srv_a"] = mock
    client._started_pids = [42]

    await client.stop_all()

    mock.stop.assert_awaited_once()
    assert len(client._transports) == 0
    assert len(client._started_pids) == 0


@pytest.mark.asyncio
async def test_stop_all_empty_transports_noop():
    """Calling stop_all with no transports is a no-op."""
    registry = MCPToolRegistry()
    client = MCPClient(configs={}, registry=registry)
    await client.stop_all()
    assert len(client._transports) == 0


@pytest.mark.asyncio
async def test_stop_all_clears_after_partial_failure():
    """Maps are cleared even when some transports fail to stop."""
    registry = MCPToolRegistry()
    configs = {
        "srv_a": MCPServerConfig(server_id="srv_a", command=["echo", "a"]),
        "srv_b": MCPServerConfig(server_id="srv_b", command=["echo", "b"]),
    }
    client = MCPClient(configs=configs, registry=registry)

    mock_a = MagicMock()
    mock_a.stop = AsyncMock(side_effect=Exception("fail"))
    mock_b = MagicMock()
    mock_b.stop = AsyncMock()

    client._transports["srv_a"] = mock_a
    client._transports["srv_b"] = mock_b
    client._started_pids = [10, 20]

    with contextlib.suppress(MCPTransportError):
        await client.stop_all()

    assert len(client._transports) == 0
    assert len(client._started_pids) == 0
    mock_b.stop.assert_awaited_once()
