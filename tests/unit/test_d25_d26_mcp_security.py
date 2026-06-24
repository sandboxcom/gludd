"""D-25 & D-26: MCP security regression tests.

D-25: tool-name collision in MCPToolRegistry must raise ValueError rather
      than silently overwriting routing for an already-registered name.
D-26: MCPClient.call_tool must validate the tool name against the registry
      before forwarding to the transport, preventing a confused-deputy
      dispatch of arbitrary tool names.

These tests pin the security gates so a future refactor cannot regress the
collision check (registry.py) or the registry-lookup guard (client.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError


class TestD25CollisionDetection:
    """D-25: registering a duplicate tool name across servers must raise."""

    def test_cross_server_name_collision_raises_value_error(self):
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="shared", description="A"))
        with pytest.raises(ValueError, match=r"(?i)collision"):
            reg.register_tool("server_b", MCPTool(name="shared", description="B"))

    def test_collision_does_not_overwrite_existing_routing(self):
        """On collision the original registration must be preserved untouched."""
        reg = MCPToolRegistry()
        original = MCPTool(name="dup", description="original", server_id="server_a")
        reg.register_tool("server_a", original)

        with pytest.raises(ValueError):
            reg.register_tool("server_b", MCPTool(name="dup", description="attacker"))

        # The attacker MUST NOT have shadowed the original.
        stored = reg.get_tool("dup", server_id="server_a")
        assert stored is original or (
            stored is not None
            and stored.description == "original"
            and stored.server_id == "server_a"
        )
        # And the attacker never got recorded.
        assert reg.get_tool("dup", server_id="server_b") is None

    def test_same_server_same_name_is_idempotent_not_collision(self):
        """Re-registering the same (server, name) pair is a benign no-op."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="t", description="v1"))
        # Must not raise — same owner, not a collision.
        reg.register_tool("server_a", MCPTool(name="t", description="v2"))
        assert len(reg.list_tools("server_a")) == 1

    def test_distinct_names_on_distinct_servers_register_cleanly(self):
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="alpha"))
        reg.register_tool("server_b", MCPTool(name="beta"))
        names = reg.tool_names()
        assert names == ["alpha", "beta"]


class TestD26CallToolRegistryValidation:
    """D-26: call_tool must refuse unregistered / wrong-server tool names."""

    @pytest.mark.asyncio
    async def test_unregistered_tool_name_is_rejected(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv", MCPTool(name="allowed"))
        client = MCPClient(
            configs={"srv": MCPServerConfig(server_id="srv", command=["echo"])},
            registry=registry,
        )
        mock_transport = MagicMock()
        mock_transport.call_tool = AsyncMock()
        client._transports["srv"] = mock_transport

        with pytest.raises(MCPTransportError, match=r"(?i)not registered"):
            await client.call_tool("srv", "evil", {})

        mock_transport.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tool_registered_to_other_server_is_rejected(self):
        """A name that exists in the registry but for a different server is
        a likely hijack and must be refused."""
        registry = MCPToolRegistry()
        # Distinct names per server (collision gate would otherwise fire).
        registry.register_tool("srv_a", MCPTool(name="a_tool"))
        registry.register_tool("srv_b", MCPTool(name="b_tool"))

        client = MCPClient(
            configs={
                "srv_a": MCPServerConfig(server_id="srv_a", command=["echo"]),
                "srv_b": MCPServerConfig(server_id="srv_b", command=["echo"]),
            },
            registry=registry,
        )
        mock_a = MagicMock()
        mock_a.call_tool = AsyncMock()
        client._transports["srv_a"] = mock_a

        # Asking srv_a to dispatch b_tool must fail (b_tool belongs to srv_b).
        with pytest.raises(MCPTransportError, match=r"(?i)not registered"):
            await client.call_tool("srv_a", "b_tool", {})

        mock_a.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_registered_tool_is_forwarded(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv", MCPTool(name="good"))
        client = MCPClient(
            configs={"srv": MCPServerConfig(server_id="srv", command=["echo"])},
            registry=registry,
        )
        mock_transport = MagicMock()
        mock_transport.call_tool = AsyncMock(return_value={"ok": True})
        client._transports["srv"] = mock_transport

        result = await client.call_tool("srv", "good", {"x": 1})
        assert result == {"ok": True}
        mock_transport.call_tool.assert_awaited_once_with("good", {"x": 1})

    @pytest.mark.asyncio
    async def test_missing_server_transport_is_rejected(self):
        registry = MCPToolRegistry()
        client = MCPClient(configs={}, registry=registry)
        with pytest.raises(MCPTransportError, match=r"(?i)no transport"):
            await client.call_tool("ghost", "any", {})
