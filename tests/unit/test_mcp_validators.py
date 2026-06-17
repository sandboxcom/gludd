"""Unit tests for the mcp._validators factory.

Tests the strip_and_require_str factory directly AND verifies that the three
models that consume it (MCPServerConfig, MCPTool, MCPCatalogEntry) still
expose exactly the same public behaviour as before the refactor.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from general_ludd.mcp._validators import strip_and_require_str
from general_ludd.mcp.catalog import MCPCatalogEntry
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool

# ---------------------------------------------------------------------------
# Factory unit tests
# ---------------------------------------------------------------------------

class _SampleModel(BaseModel):
    token: str
    _validate_token = strip_and_require_str("token")


class TestStripAndRequireStr:
    """Tests the factory independently of the three production models."""

    def test_valid_value_passes_through(self) -> None:
        m = _SampleModel(token="hello")
        assert m.token == "hello"

    def test_leading_trailing_whitespace_stripped(self) -> None:
        m = _SampleModel(token="  hello  ")
        assert m.token == "hello"

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _SampleModel(token="   ")
        assert "token must not be empty" in str(exc_info.value)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _SampleModel(token="")
        assert "token must not be empty" in str(exc_info.value)

    def test_non_string_passes_through_unchanged(self) -> None:
        # When mode="before" receives a non-str, the isinstance guard skips
        # stripping and Pydantic coerces/raises as normal.  We confirm that the
        # validator at least doesn't blow up on None — Pydantic raises its own
        # missing-field error in that case.
        with pytest.raises(ValidationError):
            _SampleModel(token=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Regression tests: MCPServerConfig still validates server_id
# ---------------------------------------------------------------------------

class TestMCPServerConfigValidatorPreserved:
    def test_valid_server_id(self) -> None:
        cfg = MCPServerConfig(server_id="my-server", command=["npx", "pkg"])
        assert cfg.server_id == "my-server"

    def test_server_id_strips(self) -> None:
        cfg = MCPServerConfig(server_id="  my-server  ", command=["npx"])
        assert cfg.server_id == "my-server"

    def test_empty_server_id_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MCPServerConfig(server_id="", command=["npx"])
        assert "server_id must not be empty" in str(exc_info.value)

    def test_whitespace_server_id_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MCPServerConfig(server_id="   ", command=["npx"])
        assert "server_id must not be empty" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Regression tests: MCPTool still validates name
# ---------------------------------------------------------------------------

class TestMCPToolValidatorPreserved:
    def test_valid_name(self) -> None:
        tool = MCPTool(name="read_file")
        assert tool.name == "read_file"

    def test_name_strips(self) -> None:
        tool = MCPTool(name="  read_file  ")
        assert tool.name == "read_file"

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MCPTool(name="")
        assert "name must not be empty" in str(exc_info.value)

    def test_whitespace_name_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MCPTool(name="   ")
        assert "name must not be empty" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Regression tests: MCPCatalogEntry still validates server_name
# ---------------------------------------------------------------------------

class TestMCPCatalogEntryValidatorPreserved:
    def test_valid_server_name(self) -> None:
        entry = MCPCatalogEntry(server_name="filesystem")
        assert entry.server_name == "filesystem"

    def test_server_name_strips(self) -> None:
        entry = MCPCatalogEntry(server_name="  filesystem  ")
        assert entry.server_name == "filesystem"

    def test_empty_server_name_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MCPCatalogEntry(server_name="")
        assert "server_name must not be empty" in str(exc_info.value)

    def test_whitespace_server_name_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MCPCatalogEntry(server_name="   ")
        assert "server_name must not be empty" in str(exc_info.value)
