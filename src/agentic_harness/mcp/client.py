from __future__ import annotations

from agentic_harness.mcp.config import MCPServerConfig
from agentic_harness.mcp.registry import MCPTool, MCPToolRegistry
from agentic_harness.mcp.transport import MCPStdioClient, MCPTransportError


class MCPClient:
    """Facade managing multiple MCP server connections."""

    def __init__(self, configs: dict[str, MCPServerConfig], registry: MCPToolRegistry) -> None:
        self._configs = configs
        self._registry = registry
        self._transports: dict[str, MCPStdioClient] = {}

    async def start_all(self) -> None:
        for server_id, config in self._configs.items():
            if not config.enabled:
                continue
            if config.is_stdio():
                transport = MCPStdioClient(config)
                await transport.start()
                tools = await transport.list_tools()
                for tool in tools:
                    self._registry.register_tool(server_id, tool)
                self._transports[server_id] = transport

    async def stop_all(self) -> None:
        for transport in self._transports.values():
            await transport.stop()
        self._transports.clear()

    async def list_tools(self, server_id: str | None = None) -> list[MCPTool]:
        return self._registry.list_tools(server_id)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> dict:
        transport = self._transports.get(server_id)
        if transport is None:
            raise MCPTransportError(f"No transport for server: {server_id}")
        return await transport.call_tool(tool_name, arguments)
