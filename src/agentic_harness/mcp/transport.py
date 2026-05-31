from __future__ import annotations

import asyncio
import json
import os

from agentic_harness.mcp.config import MCPServerConfig
from agentic_harness.mcp.registry import MCPTool


class MCPTransportError(Exception):
    pass


class MCPStdioClient:
    """Manages a single MCP server subprocess via stdio JSON-RPC."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict | None = None) -> dict:
        if self._process is None or self._process.returncode is not None:
            raise MCPTransportError("Process not running")
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        request_id = self._next_id()
        request: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params

        line = json.dumps(request) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        response_line = await self._process.stdout.readline()
        if not response_line:
            raise MCPTransportError("Connection closed")

        response = json.loads(response_line.decode())
        if "error" in response:
            raise MCPTransportError(f"JSON-RPC error: {response['error']}")

        return response.get("result", {})

    async def start(self) -> None:
        cmd = self._config.command + self._config.args
        env = {**os.environ, **self._config.env}

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hottentot-agent", "version": "0.1.0"},
            },
        )

    async def list_tools(self) -> list[MCPTool]:
        result = await self._send_request("tools/list")
        tools: list[MCPTool] = []
        for tool_data in result.get("tools", []):
            tools.append(
                MCPTool(
                    name=tool_data["name"],
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                )
            )
        return tools

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    async def stop(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
