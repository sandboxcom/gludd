"""Unit tests for src/general_ludd/mcp/client.py.

No real subprocess spawning, no real server connections.
"""

from __future__ import annotations

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_stdio_config(server_id: str = "s1", project_id: str | None = None) -> MCPServerConfig:
    return MCPServerConfig(server_id=server_id, command=["python3", "-m", "fake_mcp"], project_id=project_id)


def make_http_config(server_id: str = "s2", project_id: str | None = None) -> MCPServerConfig:
    return MCPServerConfig(server_id=server_id, url="https://example.com/mcp", project_id=project_id)


# ---------------------------------------------------------------------------
# MCPServerConfig validation
# ---------------------------------------------------------------------------

def test_mcp_server_config_stdio():
    cfg = make_stdio_config()
    assert cfg.is_stdio() is True
    assert cfg.is_http() is False


def test_mcp_server_config_http():
    cfg = make_http_config()
    assert cfg.is_stdio() is False
    assert cfg.is_http() is True


def test_mcp_server_config_requires_command_or_url():
    with pytest.raises(ValueError):
        MCPServerConfig(server_id="bad")


def test_mcp_server_config_empty_server_id_raises():
    with pytest.raises(ValueError):
        MCPServerConfig(server_id="", command=["python3"])


def test_mcp_server_config_negative_timeout_raises():
    with pytest.raises(ValueError):
        MCPServerConfig(server_id="s", command=["python3"], timeout_seconds=-1.0)


def test_mcp_server_config_project_id():
    cfg = MCPServerConfig(server_id="s", command=["python3"], project_id="proj-x")
    assert cfg.project_id == "proj-x"


# ---------------------------------------------------------------------------
# MCPToolRegistry
# ---------------------------------------------------------------------------

def test_registry_empty_list_tools():
    reg = MCPToolRegistry()
    assert reg.list_tools() == []


def test_registry_register_and_get_tool():
    reg = MCPToolRegistry()
    tool = MCPTool(name="my_tool", description="does stuff", server_id="s1")
    reg.register_tool("s1", tool)
    got = reg.get_tool("my_tool")
    assert got is not None
    assert got.name == "my_tool"
    assert got.server_id == "s1"


def test_registry_list_tools_all():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="tool_a", server_id="s1"))
    reg.register_tool("s1", MCPTool(name="tool_b", server_id="s1"))
    assert len(reg.list_tools()) == 2


def test_registry_list_tools_filter_by_server():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="tool_a", server_id="s1"))
    reg.register_tool("s2", MCPTool(name="tool_b", server_id="s2"))
    s1_tools = reg.list_tools(server_id="s1")
    assert len(s1_tools) == 1
    assert s1_tools[0].name == "tool_a"


def test_registry_list_tools_unknown_server_returns_empty():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="tool_a", server_id="s1"))
    assert reg.list_tools(server_id="s99") == []


def test_registry_get_tool_not_found():
    reg = MCPToolRegistry()
    assert reg.get_tool("nonexistent") is None


def test_registry_tool_name_collision_raises():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="shared_tool", server_id="s1"))
    with pytest.raises(ValueError, match="collision"):
        reg.register_tool("s2", MCPTool(name="shared_tool", server_id="s2"))


def test_registry_tool_names():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="b_tool", server_id="s1"))
    reg.register_tool("s1", MCPTool(name="a_tool", server_id="s1"))
    names = reg.tool_names()
    assert sorted(names) == ["a_tool", "b_tool"]


def test_registry_remove_server():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="t1", server_id="s1"))
    reg.register_tool("s1", MCPTool(name="t2", server_id="s1"))
    removed = reg.remove_server("s1")
    assert removed == 2
    assert reg.list_tools() == []


# ---------------------------------------------------------------------------
# MCPClient — start_all / stop_all with empty configs
# ---------------------------------------------------------------------------

async def test_start_all_empty_configs():
    """start_all with no configs raises no error."""
    client = MCPClient(configs={}, registry=MCPToolRegistry())
    await client.start_all()  # must not raise


async def test_stop_all_empty_transports():
    """stop_all with no transports raises no error."""
    client = MCPClient(configs={}, registry=MCPToolRegistry())
    await client.stop_all()  # must not raise


# ---------------------------------------------------------------------------
# MCPClient — list_tools
# ---------------------------------------------------------------------------

async def test_list_tools_empty_registry():
    client = MCPClient(configs={}, registry=MCPToolRegistry())
    tools = await client.list_tools()
    assert tools == []


async def test_list_tools_returns_registered():
    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="my_tool", server_id="s1"))
    client = MCPClient(configs={}, registry=reg)
    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "my_tool"


# ---------------------------------------------------------------------------
# MCPClient — list_for_project
# ---------------------------------------------------------------------------

def test_list_for_project_none_returns_all():
    cfg1 = make_stdio_config("s1", project_id="proj-a")
    cfg2 = make_http_config("s2", project_id="proj-b")
    client = MCPClient(configs={"s1": cfg1, "s2": cfg2}, registry=MCPToolRegistry())
    result = client.list_for_project(None)
    assert len(result) == 2


def test_list_for_project_filters_by_id():
    cfg1 = make_stdio_config("s1", project_id="proj-x")
    cfg2 = make_http_config("s2", project_id="proj-y")
    client = MCPClient(configs={"s1": cfg1, "s2": cfg2}, registry=MCPToolRegistry())
    result = client.list_for_project("proj-x")
    assert len(result) == 1
    assert result[0].server_id == "s1"


def test_list_for_project_no_match_returns_empty():
    cfg1 = make_stdio_config("s1", project_id="proj-a")
    client = MCPClient(configs={"s1": cfg1}, registry=MCPToolRegistry())
    result = client.list_for_project("proj-zzz")
    assert result == []


def test_list_for_project_none_project_id_not_returned_when_filtering():
    """Configs without project_id should not match a specific project filter."""
    cfg1 = make_stdio_config("s1", project_id=None)
    cfg2 = make_http_config("s2", project_id="proj-a")
    client = MCPClient(configs={"s1": cfg1, "s2": cfg2}, registry=MCPToolRegistry())
    result = client.list_for_project("proj-a")
    assert len(result) == 1
    assert result[0].server_id == "s2"


# ---------------------------------------------------------------------------
# MCPClient — call_tool errors
# ---------------------------------------------------------------------------

async def test_call_tool_no_transport_raises():
    """call_tool raises MCPTransportError when server has no transport."""
    client = MCPClient(configs={}, registry=MCPToolRegistry())
    with pytest.raises(MCPTransportError, match="No transport"):
        await client.call_tool("nonexistent", "some_tool", {})


async def test_call_tool_wrong_server_raises():
    """call_tool raises MCPTransportError for tool registered to different server."""
    from unittest.mock import AsyncMock, MagicMock

    reg = MCPToolRegistry()
    reg.register_tool("s1", MCPTool(name="owned_tool", server_id="s1"))

    transport_mock = MagicMock()
    transport_mock.call_tool = AsyncMock(return_value={"result": "ok"})

    client = MCPClient(configs={}, registry=reg)
    # Inject a fake transport for "s2" but ask for a tool owned by "s1"
    client._transports["s2"] = transport_mock

    with pytest.raises(MCPTransportError):
        await client.call_tool("s2", "owned_tool", {})


async def test_call_tool_unregistered_tool_raises():
    """call_tool raises MCPTransportError when tool not in registry at all."""
    from unittest.mock import AsyncMock, MagicMock

    reg = MCPToolRegistry()
    transport_mock = MagicMock()
    transport_mock.call_tool = AsyncMock(return_value={})

    client = MCPClient(configs={}, registry=reg)
    client._transports["s1"] = transport_mock

    with pytest.raises(MCPTransportError):
        await client.call_tool("s1", "ghost_tool", {})


# ---------------------------------------------------------------------------
# MCPClient — disabled configs skipped by start_all
# ---------------------------------------------------------------------------

async def test_start_all_skips_disabled():
    """Disabled configs must not be started (no transport created)."""
    cfg = MCPServerConfig(server_id="s_off", command=["python3"], enabled=False)
    client = MCPClient(configs={"s_off": cfg}, registry=MCPToolRegistry())
    # Should not raise and should leave transports empty
    await client.start_all()
    assert "s_off" not in client._transports


async def test_stop_all_clears_transports():
    """stop_all clears the transports dict."""
    from unittest.mock import AsyncMock, MagicMock

    transport_mock = MagicMock()
    transport_mock.stop = AsyncMock()

    client = MCPClient(configs={}, registry=MCPToolRegistry())
    client._transports["s1"] = transport_mock

    await client.stop_all()
    assert client._transports == {}
    transport_mock.stop.assert_awaited_once()
