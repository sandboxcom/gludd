"""Unit tests for MCPToolRegistry composite key storage and name-scoping.

The registry uses a (server_id, name) composite key internally. These tests
verify that:
  - Multiple tools on a single server are stored under separate composite keys.
  - get_tool(name, server_id=...) returns the correct server-scoped entry.
  - remove_server() only removes that server's tools, not another server's.
  - tool_names() returns a deduplicated sorted list across all servers.

Note: registering the *same tool name* on *two different servers* is a
security-gate violation (see TestRegistryCollisionGuard in
test_mcp_registry_gate.py) and raises ValueError. These tests therefore use
distinct tool names per server.
"""
from __future__ import annotations

import pytest

from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


class TestCompositeKeyNoCollision:
    def test_distinct_tools_two_servers_stored_independently(self):
        """Two servers each register their own tool; both are stored separately."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="tool_a", description="from A"))
        reg.register_tool("server_b", MCPTool(name="tool_b", description="from B"))

        tools_a = reg.list_tools("server_a")
        tools_b = reg.list_tools("server_b")

        assert len(tools_a) == 1
        assert tools_a[0].server_id == "server_a"
        assert tools_a[0].name == "tool_a"
        assert len(tools_b) == 1
        assert tools_b[0].server_id == "server_b"
        assert tools_b[0].name == "tool_b"

    def test_get_tool_with_server_id_returns_correct_server(self):
        """get_tool(name, server_id=...) pins to the right server."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="alpha_tool", description="from A"))
        reg.register_tool("server_b", MCPTool(name="beta_tool", description="from B"))

        t = reg.get_tool("alpha_tool", server_id="server_a")
        assert t is not None
        assert t.server_id == "server_a"

        t2 = reg.get_tool("beta_tool", server_id="server_b")
        assert t2 is not None
        assert t2.server_id == "server_b"

        # Cross-server lookup must return None (tool not registered to that server)
        assert reg.get_tool("alpha_tool", server_id="server_b") is None

    def test_remove_server_only_removes_that_servers_tools(self):
        """remove_server('server_a') must not delete server_b's tools."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="tool_a", description="A"))
        reg.register_tool("server_b", MCPTool(name="tool_b", description="B"))

        count = reg.remove_server("server_a")
        assert count == 1

        # server_b's tool must survive
        remaining = reg.list_tools("server_b")
        assert len(remaining) == 1
        assert remaining[0].server_id == "server_b"
        assert remaining[0].name == "tool_b"

        # server_a's tool must be gone
        assert reg.list_tools("server_a") == []

    def test_tool_names_includes_all_servers(self):
        """tool_names() returns a sorted list of all unique names across servers."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="alpha"))
        reg.register_tool("server_a", MCPTool(name="gamma"))
        reg.register_tool("server_b", MCPTool(name="beta"))
        names = reg.tool_names()
        assert names == ["alpha", "beta", "gamma"]

    def test_same_tool_same_server_is_idempotent(self):
        """Re-registering the same (server_id, name) pair is a no-op (not an error)."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="my_tool", description="v1"))
        reg.register_tool("server_a", MCPTool(name="my_tool", description="v2"))
        tools = reg.list_tools("server_a")
        assert len(tools) == 1

    def test_collision_between_servers_raises(self):
        """Registering the same name on a second server raises ValueError (security gate)."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="shared_tool"))
        with pytest.raises(ValueError, match="collision"):
            reg.register_tool("server_b", MCPTool(name="shared_tool"))
