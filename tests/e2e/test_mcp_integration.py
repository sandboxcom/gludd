"""E2E: MCP config loading, registry operations, and client facade."""

from __future__ import annotations

import os
import tempfile

import pytest
import yaml

from agentic_harness.mcp.client import MCPClient
from agentic_harness.mcp.config import MCPServerConfig
from agentic_harness.mcp.loader import load_mcp_config
from agentic_harness.mcp.registry import MCPTool, MCPToolRegistry


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


class TestMCPConfig:
    def test_load_example_mcp_config(self):
        path = os.path.join(_repo_root(), "config", "mcp_servers", "example.yml")
        configs = load_mcp_config(path)
        assert "filesystem" in configs
        cfg = configs["filesystem"]
        assert isinstance(cfg, MCPServerConfig)
        assert cfg.command == ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
        assert cfg.args == ["/tmp"]

    def test_mcp_config_stdio_detection(self):
        path = os.path.join(_repo_root(), "config", "mcp_servers", "example.yml")
        configs = load_mcp_config(path)
        cfg = configs["filesystem"]
        assert cfg.is_stdio() is True
        assert cfg.is_http() is False

    def test_mcp_config_requires_command_or_url(self):
        with pytest.raises(ValueError):
            MCPServerConfig(server_id="bad")

    def test_mcp_config_yaml_roundtrip(self):
        original = MCPServerConfig(
            server_id="test",
            command=["python", "-m", "some.server"],
            args=["--port", "8080"],
            env={"API_KEY": "test"},
            timeout_seconds=60.0,
            enabled=True,
        )
        data = {"servers": {"test": original.model_dump(exclude={"server_id"})}}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            yaml.dump(data, f)
            tmp_path = f.name
        try:
            reloaded = load_mcp_config(tmp_path)
            assert "test" in reloaded
            rt = reloaded["test"]
            assert rt.command == original.command
            assert rt.args == original.args
            assert rt.env == original.env
            assert rt.timeout_seconds == original.timeout_seconds
            assert rt.enabled == original.enabled
        finally:
            os.unlink(tmp_path)


class TestMCPRegistry:
    def test_mcp_registry_multi_server(self):
        reg = MCPToolRegistry()
        reg.register_tool(
            "srv-a",
            MCPTool(name="tool_a1", description="a1", input_schema={"type": "object"}),
        )
        reg.register_tool(
            "srv-b",
            MCPTool(name="tool_b1", description="b1", input_schema={"type": "object"}),
        )
        all_tools = reg.list_tools()
        assert len(all_tools) == 2
        names = {t.name for t in all_tools}
        assert names == {"tool_a1", "tool_b1"}

    def test_mcp_registry_remove_server(self):
        reg = MCPToolRegistry()
        reg.register_tool(
            "srv-a",
            MCPTool(name="keep", description="k", input_schema={}),
        )
        reg.register_tool(
            "srv-b",
            MCPTool(name="remove_me", description="r", input_schema={}),
        )
        removed = reg.remove_server("srv-b")
        assert removed == 1
        assert reg.get_tool("remove_me") is None
        assert reg.get_tool("keep") is not None

    def test_mcp_tool_has_schema(self):
        reg = MCPToolRegistry()
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        reg.register_tool(
            "srv",
            MCPTool(name="read_file", description="read", input_schema=schema),
        )
        tool = reg.get_tool("read_file")
        assert tool is not None
        assert tool.input_schema == schema


class TestMCPClientFacade:
    def test_mcp_client_facade_starts_enabled_only(self):
        configs = {
            "enabled-srv": MCPServerConfig(
                server_id="enabled-srv",
                command=["echo"],
                enabled=True,
            ),
            "disabled-srv": MCPServerConfig(
                server_id="disabled-srv",
                command=["echo"],
                enabled=False,
            ),
        }
        started_ids: list[str] = []

        class FakeTransport:
            def __init__(self, cfg):
                self._cfg = cfg

            async def start(self):
                started_ids.append(self._cfg.server_id)

            async def list_tools(self):
                return []

            async def stop(self):
                pass

        import asyncio
        from unittest.mock import patch

        reg = MCPToolRegistry()
        client = MCPClient(configs, reg)

        async def _run():
            with patch(
                "agentic_harness.mcp.client.MCPStdioClient", FakeTransport
            ):
                await client.start_all()

        asyncio.run(_run())
        assert "enabled-srv" in started_ids
        assert "disabled-srv" not in started_ids
