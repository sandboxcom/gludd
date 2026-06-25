"""Regression tests: MCPTool name validation rejects characters that break routing.

The routing layer splits qualified tool references on '/' to extract
(server_id, tool_name).  A server-supplied tool name that contains '/' would
corrupt that split and let one server shadow another server's tools.

See: src/general_ludd/mcp/registry.py _TOOL_NAME_RE
"""

from __future__ import annotations

import pytest

from general_ludd.mcp.registry import MCPTool, MCPToolRegistry

# ---------------------------------------------------------------------------
# MCPTool name validation (at construction time)
# ---------------------------------------------------------------------------


class TestMCPToolNameValidation:
    """Tool names are validated against ^[A-Za-z0-9_.:-]+$ at construction."""

    @pytest.mark.parametrize(
        "name",
        [
            "simple",
            "tool_name",
            "tool-name",
            "tool.name",
            "tool:name",
            "Tool123",
            "A",
            "a.b.c-d_e:f",
        ],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        tool = MCPTool(name=name, description="ok")
        assert tool.name == name

    @pytest.mark.parametrize(
        "bad_name",
        [
            "server/tool",        # slash — primary routing-corruption vector
            "server/sub/tool",    # multiple slashes
            "/leading-slash",
            "trailing-slash/",
            "has space",
            "has\ttab",
            "has\nnewline",
            "tool@host",
            "tool!",
            "",                   # empty after strip
            "   ",                # whitespace-only → empty after strip
        ],
    )
    def test_invalid_names_rejected(self, bad_name: str) -> None:
        with pytest.raises(Exception, match=r"tool name"):
            MCPTool(name=bad_name)


# ---------------------------------------------------------------------------
# MCPToolRegistry.register_tool honours name validation
# ---------------------------------------------------------------------------


class TestMCPToolRegistryNameValidation:
    """Registration of a tool with an invalid name must be rejected."""

    def test_slash_in_name_rejected_at_registration(self) -> None:
        registry = MCPToolRegistry()
        with pytest.raises(Exception, match=r"tool name"):
            registry.register_tool("my-server", MCPTool(name="evil/tool"))

    def test_valid_name_registers_successfully(self) -> None:
        registry = MCPToolRegistry()
        tool = MCPTool(name="valid_tool", description="fine")
        registry.register_tool("my-server", tool)
        result = registry.get_tool("valid_tool", server_id="my-server")
        assert result is not None
        assert result.name == "valid_tool"
        assert result.server_id == "my-server"

    def test_registered_tool_retrievable_by_name_only(self) -> None:
        """get_tool without server_id must still work for unambiguous names."""
        registry = MCPToolRegistry()
        registry.register_tool("srv", MCPTool(name="do_thing"))
        result = registry.get_tool("do_thing")
        assert result is not None
        assert result.name == "do_thing"
