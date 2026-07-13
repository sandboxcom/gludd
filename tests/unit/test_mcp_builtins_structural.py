"""Structural tests for mcp/builtins.py — in-process builtin MCP tools."""

from __future__ import annotations

import pytest

from general_ludd.mcp.builtins import (
    BUILTIN_SERVER_ID,
    RUN_PROJECT_CHECK_TOOL,
    WEB_RETRIEVE_TOOL,
    BuiltinToolHandler,
    register_builtins,
)


class TestConstants:
    def test_builtin_server_id(self):
        assert BUILTIN_SERVER_ID == "gludd-builtin"

    def test_run_project_check_tool(self):
        tool = RUN_PROJECT_CHECK_TOOL
        assert tool.name == "run_project_check"
        assert "check_name" in tool.input_schema.get("required", [])
        assert "check_name" in tool.input_schema.get("properties", {})

    def test_web_retrieve_tool(self):
        tool = WEB_RETRIEVE_TOOL
        assert tool.name == "web_retrieve"
        assert "url" in tool.input_schema.get("required", [])
        assert "url" in tool.input_schema.get("properties", {})


class TestBuiltinToolHandler:
    def test_default_construction(self):
        handler = BuiltinToolHandler()
        assert handler._default_workspace is None
        assert handler._web_retriever is None

    def test_with_workspace(self):
        handler = BuiltinToolHandler(default_workspace="/tmp/test")
        assert handler._default_workspace == "/tmp/test"

    def test_jail_root_defaults_to_cwd(self):
        handler = BuiltinToolHandler()
        root = handler._jail_root()
        assert root.is_absolute()

    def test_jail_root_respects_default_workspace(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            handler = BuiltinToolHandler(default_workspace=tmp)
            root = handler._jail_root()
            assert root == Path(tmp).resolve()

    def test_contain_workspace_within_jail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            handler = BuiltinToolHandler(default_workspace=tmp)
            result = handler._contain_workspace(tmp)
            assert result is not None

    def test_contain_workspace_escape_returns_none(self):
        handler = BuiltinToolHandler()
        result = handler._contain_workspace("/etc")
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        handler = BuiltinToolHandler()
        result = await handler("nonexistent_tool", {})
        assert "error" in result
        assert "unknown" in result["error"]

    @pytest.mark.asyncio
    async def test_run_project_check_missing_name(self):
        handler = BuiltinToolHandler()
        result = await handler("run_project_check", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_web_retrieve_missing_url(self):
        handler = BuiltinToolHandler()
        result = await handler("web_retrieve", {})
        assert "error" in result


class TestRegisterBuiltins:
    def test_is_callable(self):
        assert callable(register_builtins)
