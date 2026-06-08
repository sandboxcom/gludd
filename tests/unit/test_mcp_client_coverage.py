from __future__ import annotations

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError


class TestMCPClientCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_raises_when_no_transport(self):
        configs = {
            "fs": MCPServerConfig(
                server_id="fs",
                command=["npx", "-y", "server"],
            ),
        }
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)
        with pytest.raises(MCPTransportError, match="No transport for server: missing"):
            await client.call_tool("missing", "read_file", {"path": "/tmp"})


class TestMCPClientListForProject:
    def test_list_for_project_none_returns_all(self):
        configs = {
            "fs": MCPServerConfig(
                server_id="fs",
                command=["npx", "-y", "server-fs"],
                project_id="proj-1",
            ),
            "git": MCPServerConfig(
                server_id="git",
                command=["npx", "-y", "server-git"],
                project_id="proj-2",
            ),
        }
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)
        result = client.list_for_project(None)
        assert len(result) == 2
        ids = {c.server_id for c in result}
        assert ids == {"fs", "git"}

    def test_list_for_project_filters_by_project_id(self):
        configs = {
            "fs": MCPServerConfig(
                server_id="fs",
                command=["npx", "-y", "server-fs"],
                project_id="proj-1",
            ),
            "git": MCPServerConfig(
                server_id="git",
                command=["npx", "-y", "server-git"],
                project_id="proj-2",
            ),
            "web": MCPServerConfig(
                server_id="web",
                command=["npx", "-y", "server-web"],
                project_id="proj-1",
            ),
        }
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)
        result = client.list_for_project("proj-1")
        assert len(result) == 2
        assert all(c.project_id == "proj-1" for c in result)
        ids = {c.server_id for c in result}
        assert ids == {"fs", "web"}
