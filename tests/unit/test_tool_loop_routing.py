"""Tests for ToolCallLoop._resolve_server_id routing logic.

Covers:
  1. Unique name-only lookup resolves correctly.
  2. Collision (same name on two servers) → get_tool returns None.
  3. Pinned server_id lookup resolves correctly even when collision exists.
  4. _resolve_server_id raises MCPTransportError with "ambiguous" on collision.
  5. _resolve_server_id raises MCPTransportError with "not a registered" on unknown.

NOTE (hardened RC): ``MCPToolRegistry.register_tool`` now REFUSES to register
the same tool name under a different server (collision rejection — prevents a
rogue server hijacking a trusted tool name). The "ambiguous across multiple
servers" routing branch in ``_resolve_server_id`` is therefore defense-in-depth
for a collision state that can only be reached by injecting tools directly into
the registry's composite-key store, bypassing the guard. These tests build that
state via ``_inject_collision`` so the defensive routing branch is exercised.
"""
from __future__ import annotations

import pytest

from general_ludd.execution.tool_loop import ToolCallLoop
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPTransportError


def _make_loop(registry: MCPToolRegistry) -> ToolCallLoop:
    """Return a ToolCallLoop wired to *registry* (no gateway/mcp_client needed)."""
    loop = ToolCallLoop(
        model_gateway=object(),
        mcp_client=None,
        mcp_registry=registry,
    )
    return loop


def _inject_collision(reg: MCPToolRegistry, name: str, servers: list[str]) -> None:
    """Force a same-name-multiple-server state by writing the registry's internal
    composite-key store directly, bypassing the collision guard in
    ``register_tool`` (which now refuses to create such a state)."""
    for sid in servers:
        tool = MCPTool(name=name, server_id=sid)
        reg._tools[(sid, name)] = tool
        reg._server_tools.setdefault(sid, [])
        if name not in reg._server_tools[sid]:
            reg._server_tools[sid].append(name)


class TestToolLoopRouting:
    def test_unique_name_resolves_correct_server(self):
        """A tool name present on exactly one server resolves to that server."""
        reg = MCPToolRegistry()
        reg.register_tool("srv_a", MCPTool(name="read_file"))
        loop = _make_loop(reg)
        assert loop._resolve_server_id("read_file") == "srv_a"

    def test_collision_name_only_get_tool_returns_none(self):
        """get_tool(name) returns None when the same name is on two servers
        (collision state injected, since register_tool would reject it)."""
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", ["srv_a", "srv_b"])
        assert reg.get_tool("shared_tool") is None

    def test_pinned_server_id_resolves_despite_collision(self):
        """get_tool(name, server_id=...) returns the right tool even when names collide."""
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", ["srv_a", "srv_b"])
        tool = reg.get_tool("shared_tool", server_id="srv_b")
        assert tool is not None
        assert tool.server_id == "srv_b"

    def test_resolve_server_id_raises_ambiguous_on_collision(self):
        """_resolve_server_id raises MCPTransportError with 'ambiguous' when same
        tool name exists on multiple servers."""
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", ["srv_a", "srv_b"])
        loop = _make_loop(reg)
        with pytest.raises(MCPTransportError, match="ambiguous"):
            loop._resolve_server_id("shared_tool")

    def test_resolve_server_id_raises_not_registered_on_unknown(self):
        """_resolve_server_id raises MCPTransportError with 'not a registered'
        when the tool name does not appear in the registry at all."""
        reg = MCPToolRegistry()
        reg.register_tool("srv_a", MCPTool(name="read_file"))
        loop = _make_loop(reg)
        with pytest.raises(MCPTransportError, match="not a registered"):
            loop._resolve_server_id("nonexistent_tool")
