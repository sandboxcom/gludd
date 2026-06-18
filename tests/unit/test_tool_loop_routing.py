from __future__ import annotations

import pytest

from general_ludd.execution.tool_loop import ToolCallLoop
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError


class TestGetToolResolution:
    def test_unique_name_resolves(self):
        registry = MCPToolRegistry()
        tool = MCPTool(name="read_file")
        registry.register_tool("srv1", tool)
        result = registry.get_tool("read_file")
        assert result is not None
        assert result.name == "read_file"
        assert result.server_id == "srv1"

    def test_collision_name_only_returns_none(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared"))
        registry.register_tool("srv2", MCPTool(name="shared"))
        result = registry.get_tool("shared")
        assert result is None

    def test_pinned_server_id_resolves(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared"))
        registry.register_tool("srv2", MCPTool(name="shared"))
        result = registry.get_tool("shared", server_id="srv1")
        assert result is not None
        assert result.server_id == "srv1"

    def test_resolve_server_id_raises_on_collision(self):
        registry = MCPToolRegistry()
        registry.register_tool("srv1", MCPTool(name="shared"))
        registry.register_tool("srv2", MCPTool(name="shared"))

        # Build a minimal ToolCallLoop with our registry
        mock_gateway = object()
        loop = ToolCallLoop(model_gateway=mock_gateway, mcp_registry=registry)

        with pytest.raises(MCPTransportError, match="ambiguous"):
            loop._resolve_server_id("shared")

    def test_resolve_server_id_raises_on_unknown(self):
        registry = MCPToolRegistry()
        mock_gateway = object()
        loop = ToolCallLoop(model_gateway=mock_gateway, mcp_registry=registry)

        with pytest.raises(MCPTransportError, match="not a registered MCP tool"):
            loop._resolve_server_id("nonexistent_tool")
