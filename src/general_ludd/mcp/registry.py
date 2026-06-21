from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class MCPTool(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    server_id: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class MCPToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], MCPTool] = {}
        self._server_tools: dict[str, list[str]] = {}

    def register_tool(self, server_id: str, tool: MCPTool) -> None:
        tool.server_id = server_id
        self._tools[(server_id, tool.name)] = tool
        if server_id not in self._server_tools:
            self._server_tools[server_id] = []
        if tool.name not in self._server_tools[server_id]:
            self._server_tools[server_id].append(tool.name)

    def list_tools(self, server_id: str | None = None) -> list[MCPTool]:
        if server_id is not None:
            names = self._server_tools.get(server_id, [])
            return [self._tools[(server_id, n)] for n in names if (server_id, n) in self._tools]
        return list(self._tools.values())

    def get_tool(self, name: str, server_id: str | None = None) -> MCPTool | None:
        if server_id is not None:
            return self._tools.get((server_id, name))
        # Back-compat: name-only scan returns the unique match, or None if
        # zero or more than one server advertises the same name (ambiguous).
        matches = [t for ((_s, n), t) in self._tools.items() if n == name]
        return matches[0] if len(matches) == 1 else None

    def remove_server(self, server_id: str) -> int:
        names = self._server_tools.pop(server_id, [])
        for n in names:
            self._tools.pop((server_id, n), None)
        return len(names)

    def tool_names(self) -> list[str]:
        return sorted(set(n for (_, n) in self._tools))
