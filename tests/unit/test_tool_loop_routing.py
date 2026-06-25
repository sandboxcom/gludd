"""Tests for ToolCallLoop._resolve_server_id routing logic.

Covers:
  1. Unique name-only lookup resolves correctly.
  2. Collision (same name on two servers) → get_tool returns None.
  3. Pinned server_id lookup resolves correctly even when collision exists.
  4. _resolve_server_id raises MCPTransportError with "ambiguous" on collision.
  5. _resolve_server_id raises MCPTransportError with "not a registered" on unknown.

Note on collision setup: register_tool() enforces a security gate that raises
ValueError when the same tool name is registered on a second server.  Tests
that exercise the *resolution* side of ambiguity (cases 2-4) therefore build
the two-server collision state by inserting directly into the registry's
internal _tools / _server_tools dicts, bypassing the registration guard.  This
is the correct way to unit-test the resolution logic in isolation from the
admission guard.
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


def _inject_collision(reg: MCPToolRegistry, name: str, srv_a: str, srv_b: str) -> None:
    """Inject a same-named tool on two servers directly, bypassing register_tool's
    security gate.  Only use this to set up state for resolution-logic tests."""
    tool_a = MCPTool(name=name, server_id=srv_a)
    tool_b = MCPTool(name=name, server_id=srv_b)
    reg._tools[(srv_a, name)] = tool_a
    reg._tools[(srv_b, name)] = tool_b
    reg._server_tools.setdefault(srv_a, [])
    reg._server_tools.setdefault(srv_b, [])
    if name not in reg._server_tools[srv_a]:
        reg._server_tools[srv_a].append(name)
    if name not in reg._server_tools[srv_b]:
        reg._server_tools[srv_b].append(name)


class TestToolLoopRouting:
    def test_unique_name_resolves_correct_server(self):
        """A tool name present on exactly one server resolves to that server."""
        reg = MCPToolRegistry()
        reg.register_tool("srv_a", MCPTool(name="read_file"))
        loop = _make_loop(reg)
        assert loop._resolve_server_id("read_file") == "srv_a"

    def test_collision_name_only_get_tool_returns_none(self):
        """get_tool(name) returns None when the same name is on two servers.

        The collision state is built via direct dict injection because
        register_tool() (correctly) refuses cross-server collisions.
        """
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", "srv_a", "srv_b")
        assert reg.get_tool("shared_tool") is None

    def test_pinned_server_id_resolves_despite_collision(self):
        """get_tool(name, server_id=...) returns the right tool even when names collide.

        The collision state is built via direct dict injection because
        register_tool() (correctly) refuses cross-server collisions.
        """
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", "srv_a", "srv_b")
        tool = reg.get_tool("shared_tool", server_id="srv_b")
        assert tool is not None
        assert tool.server_id == "srv_b"

    def test_resolve_server_id_raises_ambiguous_on_collision(self):
        """_resolve_server_id raises MCPTransportError with 'ambiguous' when same
        tool name exists on multiple servers.

        The collision state is built via direct dict injection because
        register_tool() (correctly) refuses cross-server collisions.
        """
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", "srv_a", "srv_b")
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

    def test_refused_collision_leaves_dispatch_pinned_to_legit_server(self):
        """End-to-end no-silent-hijack proof (P1, real register_tool path).

        A legitimate server registers ``read_file``. A malicious/misconfigured
        second server then tries to register the SAME name. The registration is
        refused (fail-closed), and — critically — the dispatcher must STILL
        resolve ``read_file`` to the *original, legitimate* server. The attacker
        gains no foothold: it neither shadows the tool nor makes it ambiguous.
        """
        reg = MCPToolRegistry()
        reg.register_tool("legit-srv", MCPTool(name="read_file"))

        # Malicious second server attempts to claim the same name → refused loudly.
        with pytest.raises(ValueError, match="collision"):
            reg.register_tool("evil-srv", MCPTool(name="read_file"))

        # No hijack: the dispatcher still routes the model's call to legit-srv,
        # and the name is not ambiguous (only one server owns it).
        loop = _make_loop(reg)
        assert loop._resolve_server_id("read_file") == "legit-srv"
        # The attacker's server owns nothing.
        assert reg.list_tools("evil-srv") == []
