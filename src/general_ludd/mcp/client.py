from __future__ import annotations

import contextlib
from typing import Any

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


class MCPClient:
    """Facade managing multiple MCP server connections."""

    def __init__(
        self,
        configs: dict[str, MCPServerConfig],
        registry: MCPToolRegistry,
        secrets_mgr: Any = None,
    ) -> None:
        self._configs = configs
        self._registry = registry
        self._secrets_mgr = secrets_mgr
        self._transports: dict[str, MCPStdioClient] = {}

    async def start_all(self) -> None:
        for server_id, config in self._configs.items():
            if not config.enabled:
                continue
            if config.is_stdio():
                transport = MCPStdioClient(config, secrets_mgr=self._secrets_mgr)
                # Finding 6: if start()/list_tools() raises mid-loop, this just-
                # started transport is not yet tracked in self._transports, so
                # stop_all() would never reap it — its subprocess would leak.
                # Stop it here before re-raising so no orphan is left behind.
                try:
                    await transport.start()
                    tools = await transport.list_tools()
                except Exception:
                    with contextlib.suppress(Exception):
                        await transport.stop()
                    raise
                for tool in tools:
                    self._registry.register_tool(server_id, tool)
                self._transports[server_id] = transport

    async def stop_all(self) -> None:
        for transport in self._transports.values():
            await transport.stop()
        self._transports.clear()

    async def list_tools(self, server_id: str | None = None) -> list[MCPTool]:
        return self._registry.list_tools(server_id)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        transport = self._transports.get(server_id)
        if transport is None:
            raise MCPTransportError(f"No transport for server: {server_id}")
        tool = self._registry.get_tool(tool_name, server_id=server_id)
        if tool is None:
            raise MCPTransportError(
                f"Tool {tool_name!r} is not registered to server {server_id!r}; "
                "refusing to dispatch (possible tool-name hijack attempt)."
            )
        return await transport.call_tool(tool_name, arguments)

    def list_for_project(self, project_id: str | None) -> list[MCPServerConfig]:
        if project_id is None:
            return list(self._configs.values())
        return [c for c in self._configs.values() if c.project_id == project_id]
