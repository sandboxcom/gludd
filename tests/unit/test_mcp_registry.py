from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


class TestMCPToolModel:
    def test_tool_name_stripped(self):
        tool = MCPTool(name="  read_file  ")
        assert tool.name == "read_file"

    def test_tool_name_empty_raises(self):
        with pytest.raises(ValidationError):
            MCPTool(name="")

    def test_tool_name_whitespace_only_raises(self):
        with pytest.raises(ValidationError):
            MCPTool(name="   ")

    def test_tool_defaults(self):
        tool = MCPTool(name="my_tool")
        assert tool.description == ""
        assert tool.input_schema == {}
        assert tool.server_id == ""


class TestMCPToolRegistry:
    def test_register_and_list_all(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="tool_a"))
        registry.register_tool("srv2", MCPTool(name="tool_b"))
        tools = registry.list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"tool_a", "tool_b"}

    def test_register_sets_server_id(self):
        registry = MCPToolRegistry()
        tool = MCPTool(name="my_tool")
        registry.register_tool("my_server", tool)
        assert tool.server_id == "my_server"

    def test_list_tools_by_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="tool_a"))
        registry.register_tool("srv1", MCPTool(name="tool_b"))
        registry.register_tool("srv2", MCPTool(name="tool_c"))
        srv1_tools = registry.list_tools("srv1")
        assert len(srv1_tools) == 2
        assert {t.name for t in srv1_tools} == {"tool_a", "tool_b"}
        srv2_tools = registry.list_tools("srv2")
        assert len(srv2_tools) == 1
        assert srv2_tools[0].name == "tool_c"

    def test_list_tools_unknown_server_empty(self):
        registry = MCPToolRegistry()
        assert registry.list_tools("no_such_server") == []

    def test_get_tool_unique_name(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="read_file"))
        result = registry.get_tool("read_file")
        assert result is not None
        assert result.name == "read_file"
        assert result.server_id == "srv1"

    def test_get_tool_unknown_name_returns_none(self):
        registry = MCPToolRegistry()
        assert registry.get_tool("no_such_tool") is None

    def test_get_tool_collision_name_only_returns_none(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared"))
        registry.register_tool("srv2", MCPTool(name="shared"))
        result = registry.get_tool("shared")
        assert result is None

    def test_get_tool_with_server_id_resolves_collision(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared"))
        registry.register_tool("srv2", MCPTool(name="shared"))
        result = registry.get_tool("shared", server_id="srv1")
        assert result is not None
        assert result.server_id == "srv1"
        result2 = registry.get_tool("shared", server_id="srv2")
        assert result2 is not None
        assert result2.server_id == "srv2"

    def test_get_tool_with_server_id_missing_returns_none(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="tool_a"))
        assert registry.get_tool("tool_a", server_id="no_such_server") is None

    def test_remove_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="tool_a"))
        registry.register_tool("srv1", MCPTool(name="tool_b"))
        registry.register_tool("srv2", MCPTool(name="tool_c"))
        removed = registry.remove_server("srv1")
        assert removed == 2
        assert registry.list_tools("srv1") == []
        assert len(registry.list_tools("srv2")) == 1

    def test_remove_unknown_server_is_noop(self):
        registry = MCPToolRegistry()
        assert registry.remove_server("ghost") == 0

    def test_same_tool_name_different_servers_coexist(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared", description="from srv1"))
        registry.register_tool("srv2", MCPTool(name="shared", description="from srv2"))
        all_tools = registry.list_tools()
        assert len(all_tools) == 2
        descriptions = {t.description for t in all_tools}
        assert descriptions == {"from srv1", "from srv2"}

    def test_tool_names_no_duplicates_across_servers(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared_tool"))
        registry.register_tool("srv2", MCPTool(name="shared_tool"))
        registry.register_tool("srv1", MCPTool(name="unique_tool"))
        names = registry.tool_names()
        assert names == ["shared_tool", "unique_tool"]
        assert names.count("shared_tool") == 1

    def test_tool_names_sorted(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="z_tool"))
        registry.register_tool("srv1", MCPTool(name="a_tool"))
        registry.register_tool("srv2", MCPTool(name="m_tool"))
        names = registry.tool_names()
        assert names == sorted(names)

    def test_register_duplicate_same_server_overwrites(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="tool_a", description="v1"))
        registry.register_tool("srv1", MCPTool(name="tool_a", description="v2"))
        tools = registry.list_tools("srv1")
        assert len(tools) == 1
        assert tools[0].description == "v2"
