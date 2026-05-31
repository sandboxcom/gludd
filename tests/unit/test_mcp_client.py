"""Unit tests for MCP client skeleton."""

from __future__ import annotations

import os
import tempfile

import pytest

from agentic_harness.mcp.config import MCPServerConfig
from agentic_harness.mcp.loader import load_mcp_config
from agentic_harness.mcp.registry import MCPTool, MCPToolRegistry


class TestMCPServerConfig:
    def test_mcp_server_config_from_dict(self):
        cfg = MCPServerConfig(
            server_id="fs",
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            args=["/tmp"],
            env={"NODE_ENV": "test"},
        )
        assert cfg.server_id == "fs"
        assert cfg.command == ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
        assert cfg.args == ["/tmp"]
        assert cfg.env == {"NODE_ENV": "test"}
        assert cfg.is_stdio() is True
        assert cfg.is_http() is False

    def test_mcp_server_config_http_transport(self):
        cfg = MCPServerConfig(
            server_id="remote",
            url="http://localhost:8080/mcp",
        )
        assert cfg.url == "http://localhost:8080/mcp"
        assert cfg.command is None
        assert cfg.is_stdio() is False
        assert cfg.is_http() is True

    def test_mcp_server_config_requires_command_or_url(self):
        with pytest.raises(ValueError, match="command or url"):
            MCPServerConfig(server_id="bad")

    def test_mcp_server_config_default_timeout(self):
        cfg = MCPServerConfig(
            server_id="fs",
            command=["npx", "-y", "server"],
        )
        assert cfg.timeout_seconds == 30.0


class TestMCPTool:
    def test_mcp_tool_has_name_description_schema(self):
        tool = MCPTool(
            name="read_file",
            description="Read a file from disk",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_id="fs",
        )
        assert tool.name == "read_file"
        assert tool.description == "Read a file from disk"
        assert tool.input_schema["type"] == "object"
        assert tool.server_id == "fs"


class TestMCPToolRegistry:
    def test_mcp_tool_registry_register(self):
        registry = MCPToolRegistry()
        tool = MCPTool(name="read_file", server_id="fs")
        registry.register_tool("fs", tool)
        assert registry.get_tool("read_file") is not None

    def test_mcp_tool_registry_list_tools(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))
        registry.register_tool("git", MCPTool(name="git_status", server_id="git"))
        all_tools = registry.list_tools()
        assert len(all_tools) == 2
        names = {t.name for t in all_tools}
        assert names == {"read_file", "git_status"}

    def test_mcp_tool_registry_list_tools_for_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))
        registry.register_tool("fs", MCPTool(name="write_file", server_id="fs"))
        registry.register_tool("git", MCPTool(name="git_status", server_id="git"))
        fs_tools = registry.list_tools(server_id="fs")
        assert len(fs_tools) == 2
        assert all(t.server_id == "fs" for t in fs_tools)

    def test_mcp_tool_registry_get_tool(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))
        tool = registry.get_tool("read_file")
        assert tool is not None
        assert tool.name == "read_file"
        assert registry.get_tool("nonexistent") is None

    def test_mcp_tool_registry_remove_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))
        registry.register_tool("fs", MCPTool(name="write_file", server_id="fs"))
        registry.register_tool("git", MCPTool(name="git_status", server_id="git"))
        count = registry.remove_server("fs")
        assert count == 2
        assert registry.get_tool("read_file") is None
        assert registry.get_tool("git_status") is not None


class TestLoadMCPConfig:
    def test_load_mcp_config_from_yaml(self):
        yaml_content = (
            "servers:\n"
            "  filesystem:\n"
            "    command: ['npx', '-y', '@modelcontextprotocol/server-filesystem']\n"
            "    args: ['/tmp']\n"
            "    timeout_seconds: 30\n"
            "    enabled: true\n"
            "  git:\n"
            "    url: 'http://localhost:9000/mcp'\n"
            "    enabled: true\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            path = f.name
        try:
            configs = load_mcp_config(path)
            assert len(configs) == 2
            assert "filesystem" in configs
            assert "git" in configs
            assert configs["filesystem"].is_stdio() is True
            assert configs["git"].is_http() is True
        finally:
            os.unlink(path)

    def test_load_mcp_config_missing_file_returns_empty(self):
        configs = load_mcp_config("/nonexistent/path/mcp.yml")
        assert configs == {}
