from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


def _make_config(**overrides: object) -> MCPServerConfig:
    defaults: dict = {
        "server_id": "test-server",
        "command": ["python", "-m", "some_mcp_server"],
        "args": ["--port", "8080"],
        "env": {"FOO": "bar"},
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _init_response() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "test-server"},
        },
    }


def _mock_process(responses: list[dict]) -> MagicMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(
        side_effect=[(json.dumps(r) + "\n").encode() for r in responses]
    )
    proc.stderr = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


class TestMCPStdioClient:
    async def test_stdio_client_starts_process(self):
        config = _make_config()
        proc = _mock_process([_init_response()])

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()

        cmd = config.command + config.args
        pos_args, kwargs = mock_exec.call_args
        assert list(pos_args) == cmd
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        assert kwargs["stdout"] == asyncio.subprocess.PIPE
        assert kwargs["stderr"] == asyncio.subprocess.PIPE
        assert kwargs["env"]["FOO"] == "bar"

    async def test_stdio_client_sends_initialize(self):
        config = _make_config()
        proc = _mock_process([_init_response()])

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()

        proc.stdin.write.assert_called()
        written = proc.stdin.write.call_args[0][0]
        msg = json.loads(written.decode())
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "initialize"
        assert "protocolVersion" in msg["params"]

    async def test_stdio_client_list_tools(self):
        config = _make_config()
        tools_resp = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {"name": "read_file", "description": "Read file", "inputSchema": {"type": "object"}},
                    {"name": "write_file", "description": "Write file", "inputSchema": {"type": "object"}},
                ]
            },
        }
        proc = _mock_process([_init_response(), tools_resp])

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()
            tools = await client.list_tools()

        assert len(tools) == 2
        assert tools[0].name == "read_file"
        assert tools[1].name == "write_file"

    async def test_stdio_client_call_tool(self):
        config = _make_config()
        call_resp = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": "file contents"}]},
        }
        proc = _mock_process([_init_response(), call_resp])

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()
            result = await client.call_tool("read_file", {"path": "/tmp/foo"})

        assert result["content"][0]["text"] == "file contents"

    async def test_stdio_client_stop_terminates_process(self):
        config = _make_config()
        proc = _mock_process([_init_response()])

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()
            await client.stop()

        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()

    async def test_stdio_client_handles_process_error(self):
        config = _make_config()
        proc = _mock_process([_init_response()])

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()

        proc.returncode = 1

        with pytest.raises(MCPTransportError):
            await client.list_tools()


class TestMCPClientFacade:
    def _make_configs(self) -> dict[str, MCPServerConfig]:
        return {
            "srv1": _make_config(server_id="srv1", command=["cmd1"]),
            "srv2": _make_config(server_id="srv2", command=["cmd2"]),
            "disabled": _make_config(server_id="disabled", command=["cmd3"], enabled=False),
        }

    async def test_mcp_client_facade_list_tools(self):
        configs = {"srv": _make_config()}
        registry = MCPToolRegistry()

        mock_transport = MagicMock()
        mock_transport.start = AsyncMock()
        mock_transport.stop = AsyncMock()
        mock_transport.list_tools = AsyncMock(
            return_value=[MCPTool(name="read_file", description="Read file")]
        )

        with patch("general_ludd.mcp.client.MCPStdioClient", return_value=mock_transport):
            client = MCPClient(configs, registry)
            await client.start_all()
            tools = await client.list_tools("srv")

        assert len(tools) == 1
        assert tools[0].name == "read_file"

    async def test_mcp_client_facade_call_tool(self):
        configs = {"srv": _make_config()}
        registry = MCPToolRegistry()

        mock_transport = MagicMock()
        mock_transport.start = AsyncMock()
        mock_transport.stop = AsyncMock()
        mock_transport.list_tools = AsyncMock(return_value=[])
        mock_transport.call_tool = AsyncMock(
            return_value={"content": [{"type": "text", "text": "ok"}]}
        )

        with patch("general_ludd.mcp.client.MCPStdioClient", return_value=mock_transport):
            client = MCPClient(configs, registry)
            await client.start_all()
            result = await client.call_tool("srv", "read_file", {"path": "/tmp/f"})

        mock_transport.call_tool.assert_called_once_with("read_file", {"path": "/tmp/f"})
        assert result["content"][0]["text"] == "ok"

    async def test_mcp_client_facade_start_stop(self):
        configs = self._make_configs()
        registry = MCPToolRegistry()

        mock_transport = MagicMock()
        mock_transport.start = AsyncMock()
        mock_transport.stop = AsyncMock()
        mock_transport.list_tools = AsyncMock(return_value=[])

        with patch("general_ludd.mcp.client.MCPStdioClient", return_value=mock_transport):
            client = MCPClient(configs, registry)
            await client.start_all()
            assert mock_transport.start.call_count == 2

            await client.stop_all()
            assert mock_transport.stop.call_count == 2
