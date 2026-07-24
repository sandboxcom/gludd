from __future__ import annotations

import contextlib
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


class _MCPTransport(Protocol):
    """Structural interface every transport (real stdio or synthetic builtin)
    must expose so :class:`MCPClient` can drive it uniformly.

    ``MCPStdioClient`` already satisfies this (its ``call_tool``/``stop`` match).
    """

    @property
    def pid(self) -> int | None: ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    async def stop(self) -> None: ...


# Handler signature for an in-process builtin server: given the tool name and
# its arguments, return the tool result as a JSON-serialisable dict.
BuiltinHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class _BuiltinTransport:
    """Synthetic in-process transport backing a "builtin" MCP server.

    It exposes the same async ``call_tool(tool_name, arguments) -> dict``
    interface as :class:`MCPStdioClient` but forwards to a Python coroutine
    handler instead of a subprocess. ``start``/``stop`` are no-ops so the
    builtin plays nicely with ``start_all``/``stop_all`` without leaking any
    process.
    """

    def __init__(self, handler: BuiltinHandler) -> None:
        self._handler = handler

    @property
    def pid(self) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._handler(tool_name, arguments)


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
        self._transports: dict[str, _MCPTransport] = {}
        self._started_pids: list[int] = []

    async def start_all(self) -> None:
        for server_id, config in self._configs.items():
            if not config.enabled:
                continue
            if config.is_stdio():
                transport = MCPStdioClient(config, secrets_mgr=self._secrets_mgr)
                # Finding 6 / H.15: if start()/list_tools() raises mid-loop, we
                # must stop BOTH the failing transport AND every previously-
                # started transport already tracked in self._transports.
                # Otherwise servers A, B, … that succeeded before server N
                # failed are orphaned — stop_all() is never called because
                # the exception propagates out.
                try:
                    await transport.start()
                    tools = await transport.list_tools()
                except Exception:
                    with contextlib.suppress(Exception):
                        await transport.stop()
                    for _tid, t in list(self._transports.items()):
                        with contextlib.suppress(Exception):
                            await t.stop()
                    for kill_pid in self._started_pids:
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.kill(kill_pid, signal.SIGKILL)
                    self._transports.clear()
                    self._started_pids.clear()
                    raise
                for tool in tools:
                    self._registry.register_tool(server_id, tool)
                self._transports[server_id] = transport
                tpid = getattr(transport, "pid", None)
                if tpid is not None:
                    self._started_pids.append(tpid)

    def register_builtin(
        self,
        server_id: str,
        tools: list[MCPTool],
        handler: BuiltinHandler,
    ) -> None:
        """Register an in-process "builtin" server whose tools are backed by a
        Python coroutine ``handler`` instead of a subprocess.

        Each tool is registered in the shared registry (so it appears in
        ``list_tools`` and passes the ToolCallLoop capability gate) and a
        synthetic transport is inserted into ``self._transports`` under
        ``server_id`` so ``call_tool(server_id, name, args)`` dispatches to
        ``handler(name, args)``. Idempotent-safe: re-registering the same
        server replaces its transport; the registry rejects cross-server name
        collisions as usual.
        """
        for tool in tools:
            self._registry.register_tool(server_id, tool)
        self._transports[server_id] = _BuiltinTransport(handler)

    async def stop_all(self) -> None:
        failures: list[str] = []
        for server_id, transport in self._transports.items():
            try:
                await transport.stop()
            except Exception as exc:
                failures.append(f"{server_id}: {exc}")
        self._transports.clear()
        self._started_pids.clear()
        if failures:
            raise MCPTransportError(
                f"Failed to stop {len(failures)} transport(s): {'; '.join(failures)}"
            )

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
