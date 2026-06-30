"""OrnithMCPClientAdapter — managed MCP transport for the Ornith MCP server.

Creates an MCPServerConfig, manages MCPStdioClient lifecycle, and exposes
solve / improve / resource access through the standard MCP tool/resource
protocol.  Follows the same patterns as MCPClient in ``src/general_ludd/mcp/``.
"""

from __future__ import annotations

from typing import Any

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import MCPStdioClient


class OrnithMCPClientAdapter:
    """Adapter that runs the Ornith MCP server as a managed MCP stdio client.

    Usage::

        adapter = OrnithMCPClientAdapter(enabled=True)
        await adapter.start()
        result = await adapter.solve("fix the bug", "/repo")
        status = await adapter.read_resource("ornith://ornith_status")
        await adapter.stop()

    The adapter is the transport layer only.  Higher-level concerns
    (permission checks, STS token minting, audit recording) are handled
    by ``OrnithClient`` in ``client.py``.
    """

    SERVER_ID = "ornith"

    def __init__(
        self,
        ornith_binary: str = "ornith",
        enabled: bool = False,
        timeout_seconds: int = 600,
    ) -> None:
        self._binary = ornith_binary
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._transport: MCPStdioClient | None = None
        self._config: MCPServerConfig | None = None

    def build_config(self) -> MCPServerConfig:
        """Build an MCPServerConfig pointing at the Ornith MCP server binary."""
        return MCPServerConfig(
            server_id=self.SERVER_ID,
            command=[self._binary, "--json"],
            env={"ORNITH_ENABLED": "1" if self._enabled else "0"},
            timeout_seconds=self._timeout,
            enabled=self._enabled,
        )

    async def start(self) -> None:
        """Start the Ornith MCP server subprocess.

        No-op when ``enabled=False``.
        """
        if not self._enabled:
            return
        self._config = self.build_config()
        self._transport = MCPStdioClient(self._config)
        await self._transport.start()

    async def stop(self) -> None:
        """Terminate the Ornith MCP server subprocess."""
        if self._transport is not None:
            await self._transport.stop()
            self._transport = None

    async def solve(
        self,
        task_description: str,
        repo_context_path: str,
        max_iterations: int = 10,
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Call the ``ornith_solve`` tool on the Ornith MCP server."""
        if self._transport is None:
            raise RuntimeError("Ornith adapter not started")
        return await self._transport.call_tool(
            "ornith_solve",
            {
                "task_description": task_description,
                "repo_context_path": repo_context_path,
                "max_iterations": max_iterations,
                "target_files": target_files or [],
            },
        )

    async def improve(
        self,
        target_artifact_path: str,
        feedback_yaml: str,
        artifact_kind: str,
    ) -> dict[str, Any]:
        """Call the ``ornith_improve`` tool on the Ornith MCP server."""
        if self._transport is None:
            raise RuntimeError("Ornith adapter not started")
        return await self._transport.call_tool(
            "ornith_improve",
            {
                "target_artifact_path": target_artifact_path,
                "feedback_yaml": feedback_yaml,
                "artifact_kind": artifact_kind,
            },
        )

    async def list_resources(self) -> list[dict[str, Any]]:
        """List resources exposed by the Ornith MCP server."""
        if self._transport is None:
            raise RuntimeError("Ornith adapter not started")
        return await self._transport.list_resources()

    async def read_resource(self, resource_uri: str) -> dict[str, Any]:
        """Read a resource from the Ornith MCP server by URI.

        Standard URIs exposed by the Ornith server::

            ornith://ornith_status
            ornith://ornith_model_info
        """
        if self._transport is None:
            raise RuntimeError("Ornith adapter not started")
        return await self._transport.read_resource(resource_uri)

    @property
    def transport(self) -> MCPStdioClient | None:
        return self._transport

    @property
    def config(self) -> MCPServerConfig | None:
        return self._config
