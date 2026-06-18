"""Unit tests for MCPToolRegistry composite key and collision fix."""
from __future__ import annotations

from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


class TestCompositeKeyNoCollision:
    def test_same_tool_name_two_servers_no_clobber(self):
        """F1: two servers registering the same tool name must not clobber each other."""
        reg = MCPToolRegistry()
        tool_a = MCPTool(name="shared_tool", description="from A")
        tool_b = MCPTool(name="shared_tool", description="from B")
        reg.register_tool("server_a", tool_a)
        reg.register_tool("server_b", tool_b)

        tools_a = reg.list_tools("server_a")
        tools_b = reg.list_tools("server_b")

        assert len(tools_a) == 1
        assert tools_a[0].server_id == "server_a"
        assert len(tools_b) == 1
        assert tools_b[0].server_id == "server_b"

    def test_get_tool_with_server_id_returns_correct_server(self):
        """get_tool(name, server_id=...) pins to the right server."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="shared_tool", description="from A"))
        reg.register_tool("server_b", MCPTool(name="shared_tool", description="from B"))

        t = reg.get_tool("shared_tool", server_id="server_a")
        assert t is not None
        assert t.server_id == "server_a"

        t2 = reg.get_tool("shared_tool", server_id="server_b")
        assert t2 is not None
        assert t2.server_id == "server_b"

    def test_remove_server_only_removes_that_servers_tools(self):
        """remove_server('server_a') must not delete server_b's same-named tool."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="shared_tool", description="A"))
        reg.register_tool("server_b", MCPTool(name="shared_tool", description="B"))

        count = reg.remove_server("server_a")
        assert count == 1

        # server_b's tool must survive
        remaining = reg.list_tools("server_b")
        assert len(remaining) == 1
        assert remaining[0].server_id == "server_b"

    def test_tool_names_no_duplicates_across_servers(self):
        """tool_names() returns one entry per (server,name) pair."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="shared_tool"))
        reg.register_tool("server_b", MCPTool(name="shared_tool"))
        # After fix: two distinct entries in _tools keyed by composite
        names = reg.tool_names()
        assert len(names) == 2
