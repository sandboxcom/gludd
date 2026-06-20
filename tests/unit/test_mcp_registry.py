"""Unit tests for MCPToolRegistry composite key and collision fix.

The hardened registry (RC) REJECTS cross-server tool-name collisions at
``register_tool`` time: a tool name already advertised by one server cannot be
re-registered under a different server (that would let a rogue server hijack a
trusted tool name). The registry is still keyed by a composite
``(server_id, name)`` tuple, so:

  * same name on the SAME server overwrites (idempotent re-register),
  * same name on a DIFFERENT server raises ``ValueError`` ("Tool name
    collision: ..."),
  * ``get_tool(name)`` (name-only) returns the unique match or ``None`` when
    zero or more than one server advertises the name (defensive: a collision
    state can only arise if tools are injected bypassing ``register_tool``),
  * ``get_tool(name, server_id=...)`` pins to one server,
  * ``tool_names()`` deduplicates names across servers.
"""
from __future__ import annotations

import pytest

from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


def _inject_collision(reg: MCPToolRegistry, name: str, servers: list[str]) -> None:
    """Force a same-name-multiple-server state by writing the registry's internal
    composite-key store directly, bypassing the collision guard in
    ``register_tool``. This is the only way to exercise the registry's defensive
    name-only/dedup behavior, since ``register_tool`` now refuses to create such
    a state on its own.
    """
    for sid in servers:
        tool = MCPTool(name=name, server_id=sid)
        reg._tools[(sid, name)] = tool
        reg._server_tools.setdefault(sid, [])
        if name not in reg._server_tools[sid]:
            reg._server_tools[sid].append(name)


class TestCompositeKeyNoCollision:
    def test_same_tool_name_two_servers_rejected(self):
        """F1 (hardened): a second server registering an already-registered tool
        name must be REFUSED — it cannot clobber or hijack the first server's
        tool. The guard raises ValueError with a 'Tool name collision' message.
        """
        reg = MCPToolRegistry()
        tool_a = MCPTool(name="shared_tool", description="from A")
        tool_b = MCPTool(name="shared_tool", description="from B")
        reg.register_tool("server_a", tool_a)
        with pytest.raises(ValueError, match="Tool name collision"):
            reg.register_tool("server_b", tool_b)

        # server_a's tool is untouched; server_b has no tools.
        tools_a = reg.list_tools("server_a")
        assert len(tools_a) == 1
        assert tools_a[0].server_id == "server_a"
        assert reg.list_tools("server_b") == []

    def test_same_server_same_name_overwrites(self):
        """Re-registering the same name on the SAME server is idempotent overwrite,
        not a collision."""
        reg = MCPToolRegistry()
        reg.register_tool("server_a", MCPTool(name="shared_tool", description="v1"))
        reg.register_tool("server_a", MCPTool(name="shared_tool", description="v2"))
        tools_a = reg.list_tools("server_a")
        assert len(tools_a) == 1
        assert tools_a[0].description == "v2"

    def test_get_tool_with_server_id_returns_correct_server(self):
        """get_tool(name, server_id=...) pins to the right server when a collision
        state exists (built directly, since register_tool would refuse it)."""
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", ["server_a", "server_b"])

        t = reg.get_tool("shared_tool", server_id="server_a")
        assert t is not None
        assert t.server_id == "server_a"

        t2 = reg.get_tool("shared_tool", server_id="server_b")
        assert t2 is not None
        assert t2.server_id == "server_b"

    def test_remove_server_only_removes_that_servers_tools(self):
        """remove_server('server_a') must not delete server_b's same-named tool
        (composite-key isolation), even in an injected collision state."""
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", ["server_a", "server_b"])

        count = reg.remove_server("server_a")
        assert count == 1

        # server_b's tool must survive
        remaining = reg.list_tools("server_b")
        assert len(remaining) == 1
        assert remaining[0].server_id == "server_b"

    def test_tool_names_no_duplicates_across_servers(self):
        """tool_names() deduplicates names across servers — same name on two
        servers counts once; a unique name on a third server is also included."""
        reg = MCPToolRegistry()
        _inject_collision(reg, "shared_tool", ["server_a", "server_b"])
        reg.register_tool("server_a", MCPTool(name="unique_tool"))
        names = reg.tool_names()
        assert names == ["shared_tool", "unique_tool"]
