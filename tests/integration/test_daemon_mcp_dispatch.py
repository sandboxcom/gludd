"""Integration: the daemon's MCP tool-call dispatch path is actually WIRED.

Completion-integrity HIGH regression guard.

THE BUG (audit a30dc5ac): the daemon constructs an MCPClient + MCPToolRegistry
and passes them to the EventLoop, but never builds/binds a DynamicDispatcher
with an ``mcp`` handler. At dispatch time the EventLoop sees ``_dispatcher is
None`` and logs "no dispatcher is wired — skipping dispatch", silently DROPPING
a model-emitted MCP tool call. ``_mcp_client`` was only used to *advertise* tool
names, never to *execute* one.

This test drives the REAL path the daemon uses:

  * a ``DynamicDispatcher`` built exactly the way the daemon's
    :func:`general_ludd.daemon.build_event_loop_mcp_dispatcher` builds it,
  * wired into a real ``EventLoop`` via ``dispatcher=``,
  * a model response carrying an ``mcp`` tool_call,

and asserts the call is ROUTED to ``mcp_client.call_tool`` (recorded by a fake
client) and the result flows back through the DispatchResult — NOT a mock that
bypasses the dispatcher.

Before the fix ``build_event_loop_mcp_dispatcher`` does not exist (ImportError)
and the EventLoop dispatch site has no dispatcher, so this test FAILS. After the
fix it imports, the role is permitted ("event_loop"), and the call is executed.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.dispatch.dynamic_dispatcher import ToolCall
from general_ludd.event_loop.loop import EventLoop
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


class _FakeTransport:
    """Records call_tool invocations and returns a canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, arguments))
        return {"content": f"executed:{tool_name}", "args": arguments}


class _FakeMCPClient:
    """A real-shaped MCPClient stand-in that RECORDS call_tool.

    Mirrors :class:`general_ludd.mcp.client.MCPClient` — same
    ``call_tool(server_id, tool_name, arguments)`` signature and the same
    registry-backed server_id validation — so the dispatcher must reach the
    genuine routing path, not a bypass.
    """

    def __init__(self, registry: MCPToolRegistry) -> None:
        self._registry = registry
        self._transports: dict[str, _FakeTransport] = {}
        self.recorded: list[tuple[str, str, dict[str, Any]]] = []

    def add_server(self, server_id: str, transport: _FakeTransport) -> None:
        self._transports[server_id] = transport

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.recorded.append((server_id, tool_name, arguments))
        transport = self._transports.get(server_id)
        if transport is None:
            raise RuntimeError(f"No transport for server: {server_id}")
        tool = self._registry.get_tool(tool_name)
        if tool is None or tool.server_id != server_id:
            raise RuntimeError(
                f"Tool {tool_name!r} not registered to server {server_id!r}"
            )
        return await transport.call_tool(tool_name, arguments)


def _build_registry_and_client() -> tuple[MCPToolRegistry, _FakeMCPClient]:
    registry = MCPToolRegistry()
    registry.register_tool("files", MCPTool(name="read_file", description="read"))
    client = _FakeMCPClient(registry)
    client.add_server("files", _FakeTransport())
    return registry, client


class TestDaemonMcpDispatchWired:
    def test_builder_exists(self) -> None:
        """The daemon must expose a builder that wires the EventLoop dispatcher.

        Before the fix this import fails — the daemon never builds a dispatcher
        for the EventLoop, so the symbol does not exist.
        """
        from general_ludd.daemon import build_event_loop_mcp_dispatcher  # noqa: F401

    def test_dispatcher_routes_mcp_call_to_client(self) -> None:
        """A model MCP tool_call is ROUTED to mcp_client.call_tool and returns."""
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        registry, client = _build_registry_and_client()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=client,
            mcp_tool_registry=registry,
            skill_registry=None,
            agent_dispatcher=None,
        )

        # The model emitted: call MCP tool "read_file" on server "files".
        call = ToolCall(kind="mcp", name="files/read_file", args={"path": "/etc/hosts"})
        result = dispatcher.dispatch(call)

        assert result.ok is True, f"dispatch failed: {result.error!r}"
        # Recorded on the REAL client path — not bypassed.
        assert client.recorded == [("files", "read_file", {"path": "/etc/hosts"})]
        # The transport result flowed back through the DispatchResult.
        assert result.output == {
            "content": "executed:read_file",
            "args": {"path": "/etc/hosts"},
        }

    @pytest.mark.asyncio
    async def test_eventloop_dispatch_site_executes_mcp_call(self) -> None:
        """Drive the EventLoop's own dispatch site: dispatch_all → client.

        This is the exact code path loop.py runs after parsing model tool_calls
        (``self._dispatcher.dispatch_all(calls)``). With the bug, ``_dispatcher``
        is None and the call is dropped; with the fix the wired dispatcher
        executes it.
        """
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        registry, client = _build_registry_and_client()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=client,
            mcp_tool_registry=registry,
            skill_registry=None,
            agent_dispatcher=None,
        )

        loop = EventLoop(dispatcher=dispatcher)
        assert loop._dispatcher is not None

        calls = [ToolCall(kind="mcp", name="files/read_file", args={"q": 1})]
        results = loop._dispatcher.dispatch_all(calls)

        assert len(results) == 1
        assert results[0].ok is True, f"dispatch failed: {results[0].error!r}"
        assert client.recorded == [("files", "read_file", {"q": 1})]

    def test_role_is_event_loop_not_denied(self) -> None:
        """The wired dispatcher must use a role that may dispatch mcp.

        Guards against the capability_denied trap (role None denies mcp).
        """
        from general_ludd.daemon import build_event_loop_mcp_dispatcher

        registry, client = _build_registry_and_client()
        dispatcher = build_event_loop_mcp_dispatcher(
            mcp_client=client,
            mcp_tool_registry=registry,
            skill_registry=None,
            agent_dispatcher=None,
        )
        # mcp kind must be registered AND the role permitted.
        assert "mcp" in dispatcher.list_available()["registered_kinds"]
        result = dispatcher.dispatch(
            ToolCall(kind="mcp", name="files/read_file", args={})
        )
        assert "capability_denied" not in (result.error or "")
