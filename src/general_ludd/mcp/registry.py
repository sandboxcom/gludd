"""Fail-closed MCP tool registration and server-bound lookup."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from general_ludd.mcp._validators import TrimmedNonEmptyStr

# Tool names are used as the right-hand component of a "server_id/tool_name"
# routing key.  A name containing '/' would corrupt the split and allow one
# server to shadow another server's tools.  Whitespace-only names survive the
# outer strip but are still semantically empty.  Reject all three up-front.
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class MCPTool(BaseModel):
    """Describe a validated MCP tool advertised by one server."""

    name: TrimmedNonEmptyStr
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_id: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _require_safe_name(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("tool name must not be empty")
        if not _TOOL_NAME_RE.match(v):
            raise ValueError(
                f"invalid tool name {v!r}: must match ^[A-Za-z0-9_.:-]+$ "
                "(slash, whitespace, and other special characters are not allowed)"
            )
        return v


class MCPToolRegistry:
    """Store MCP tools by server and prevent unqualified-name hijacking."""

    def __init__(self) -> None:
        """Initialize empty composite and per-server indexes."""
        self._tools: dict[tuple[str, str], MCPTool] = {}
        self._server_tools: dict[str, list[str]] = {}

    def register_tool(self, server_id: str, tool: MCPTool) -> None:
        """Register a tool unless another server already owns its name."""
        # Security gate: reject tool-name collision across servers.
        # If the same name is already registered to a *different* server, that
        # is a configuration error (or a malicious server trying to shadow an
        # existing tool) and must be refused.
        existing = self._tools.get((server_id, tool.name))
        if existing is None:
            # Check whether any other server owns this name.
            for (sid, n), _t in self._tools.items():
                if n == tool.name and sid != server_id:
                    raise ValueError(
                        f"Tool name collision: '{tool.name}' is already owned by "
                        f"server '{sid}'; server '{server_id}' cannot register it."
                    )

        tool.server_id = server_id
        self._tools[(server_id, tool.name)] = tool
        if server_id not in self._server_tools:
            self._server_tools[server_id] = []
        if tool.name not in self._server_tools[server_id]:
            self._server_tools[server_id].append(tool.name)

    def list_tools(self, server_id: str | None = None) -> list[MCPTool]:
        """List all tools or only tools registered to ``server_id``."""
        if server_id is not None:
            names = self._server_tools.get(server_id, [])
            return [self._tools[(server_id, n)] for n in names if (server_id, n) in self._tools]
        return list(self._tools.values())

    def get_tool(self, name: str, server_id: str | None = None) -> MCPTool | None:
        """Return a server-pinned tool or one unique unqualified match."""
        if server_id is not None:
            return self._tools.get((server_id, name))
        # Back-compat: name-only scan returns the unique match, or None if
        # zero or more than one server advertises the same name (ambiguous).
        matches = [t for ((_s, n), t) in self._tools.items() if n == name]
        return matches[0] if len(matches) == 1 else None

    def remove_server(self, server_id: str) -> int:
        """Remove one server's tools and return the number removed."""
        names = self._server_tools.pop(server_id, [])
        for n in names:
            self._tools.pop((server_id, n), None)
        return len(names)

    def tool_names(self) -> list[str]:
        """Return sorted unique tool names across registered servers."""
        return sorted(set(n for (_, n) in self._tools))
