"""Contract tests for reusable MCP string constraints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.mcp.catalog import MCPCatalogEntry
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool


def test_non_empty_constraints_are_visible_in_generated_schemas() -> None:
    cases = (
        (MCPServerConfig, "server_id"),
        (MCPCatalogEntry, "server_name"),
        (MCPTool, "name"),
    )
    for model_type, field_name in cases:
        field_schema = model_type.model_json_schema()["properties"][field_name]
        assert field_schema["minLength"] == 1


def test_consumer_models_strip_outer_whitespace() -> None:
    config = MCPServerConfig(server_id="  server-a  ", command=["python", "-m", "server"])
    catalog_entry = MCPCatalogEntry(server_name="  filesystem  ")
    tool = MCPTool(name="  read_file  ")

    assert config.server_id == "server-a"
    assert catalog_entry.server_name == "filesystem"
    assert tool.name == "read_file"


@pytest.mark.parametrize("value", ["", "   "])
def test_consumer_models_reject_empty_or_whitespace_only_values(value: str) -> None:
    with pytest.raises(ValidationError):
        MCPServerConfig(server_id=value, command=["python", "-m", "server"])
    with pytest.raises(ValidationError):
        MCPCatalogEntry(server_name=value)
    with pytest.raises(ValidationError):
        MCPTool(name=value)


def test_tool_keeps_domain_specific_safe_name_gate() -> None:
    with pytest.raises(ValidationError, match="invalid tool name"):
        MCPTool(name="server/read_file")
