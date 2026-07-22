"""Unit tests for MCP client skeleton."""

from __future__ import annotations

import os
import tempfile

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.loader import load_mcp_config
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


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


class TestCallToolConfusedDeputyGuard:
    """F2: call_tool must reject tool names not registered for that server."""

    @pytest.mark.asyncio
    async def test_call_tool_rejects_unregistered_tool(self):
        from unittest.mock import AsyncMock, MagicMock

        from general_ludd.mcp.client import MCPClient
        from general_ludd.mcp.transport import MCPTransportError

        registry = MCPToolRegistry()
        registry.register_tool("srv", MCPTool(name="allowed_tool"))

        config = {"srv": MCPServerConfig(server_id="srv", command=["echo"])}
        client = MCPClient(configs=config, registry=registry)

        mock_transport = MagicMock()
        mock_transport.call_tool = AsyncMock()
        client._transports["srv"] = mock_transport

        with pytest.raises(MCPTransportError, match="not registered"):
            await client.call_tool("srv", "evil_tool", {})

        mock_transport.call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_call_tool_allows_registered_tool(self):
        from unittest.mock import AsyncMock, MagicMock

        from general_ludd.mcp.client import MCPClient

        registry = MCPToolRegistry()
        registry.register_tool("srv", MCPTool(name="allowed_tool"))

        config = {"srv": MCPServerConfig(server_id="srv", command=["echo"])}
        client = MCPClient(configs=config, registry=registry)

        mock_transport = MagicMock()
        mock_transport.call_tool = AsyncMock(return_value={"result": "ok"})
        client._transports["srv"] = mock_transport

        result = await client.call_tool("srv", "allowed_tool", {"arg": 1})
        assert result == {"result": "ok"}
        mock_transport.call_tool.assert_awaited_once_with("allowed_tool", {"arg": 1})


class TestMCPClientTransportLifecycle:
    @pytest.mark.asyncio
    async def test_start_all_accepts_transport_without_pid(self, monkeypatch):
        from general_ludd.mcp import client as mcp_client
        from general_ludd.mcp.client import MCPClient

        class FakeTransport:
            def __init__(self, config, secrets_mgr=None):
                self.config = config
                self.started = False

            async def start(self):
                self.started = True

            async def stop(self):
                self.started = False

            async def list_tools(self):
                return [MCPTool(name="fake_tool", server_id="srv")]

            async def call_tool(self, tool_name, arguments):
                return {"tool_name": tool_name, "arguments": arguments}

        monkeypatch.setattr(mcp_client, "MCPStdioClient", FakeTransport)

        registry = MCPToolRegistry()
        client = MCPClient(
            configs={"srv": MCPServerConfig(server_id="srv", command=["fake-mcp"])},
            registry=registry,
        )

        await client.start_all()

        assert "srv" in client._transports
        assert client._started_pids == []
        assert registry.get_tool("fake_tool", server_id="srv") is not None
