"""Structural tests for mcp/builtins.py — in-process builtin MCP tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.mcp.builtins import (
    BUILTIN_SERVER_ID,
    RUN_PROJECT_CHECK_TOOL,
    WEB_RETRIEVE_TOOL,
    BuiltinToolHandler,
    register_builtins,
)


class TestConstants:
    def test_builtin_server_id(self) -> None:
        assert BUILTIN_SERVER_ID == "gludd-builtin"

    def test_run_project_check_tool(self) -> None:
        tool = RUN_PROJECT_CHECK_TOOL
        assert tool.name == "run_project_check"
        assert "check_name" in tool.input_schema.get("required", [])
        assert "check_name" in tool.input_schema.get("properties", {})

    def test_web_retrieve_tool(self) -> None:
        tool = WEB_RETRIEVE_TOOL
        assert tool.name == "web_retrieve"
        assert "url" in tool.input_schema.get("required", [])
        assert "url" in tool.input_schema.get("properties", {})


class TestBuiltinToolHandler:
    def test_default_construction(self) -> None:
        handler = BuiltinToolHandler()
        assert handler._default_workspace is None
        assert handler._web_retriever is None

    def test_with_workspace(self) -> None:
        handler = BuiltinToolHandler(default_workspace="/tmp/test")
        assert handler._default_workspace == "/tmp/test"

    def test_jail_root_defaults_to_cwd(self) -> None:
        handler = BuiltinToolHandler()
        root = handler._jail_root()
        assert root.is_absolute()

    def test_jail_root_respects_default_workspace(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            handler = BuiltinToolHandler(default_workspace=tmp)
            root = handler._jail_root()
            assert root == Path(tmp).resolve()

    def test_contain_workspace_within_jail(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            handler = BuiltinToolHandler(default_workspace=tmp)
            result = handler._contain_workspace(tmp)
            assert result is not None

    def test_default_workspace_isolated_from_ambient_project_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        ambient_root = tmp_path / "ambient"
        explicit_workspace = tmp_path / "explicit"
        ambient_root.mkdir()
        explicit_workspace.mkdir()
        monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(ambient_root))

        handler = BuiltinToolHandler(default_workspace=explicit_workspace)

        assert handler._jail_root() == explicit_workspace.resolve()
        assert handler._contain_workspace(str(explicit_workspace)) == explicit_workspace.resolve()
        assert handler._contain_workspace(str(ambient_root)) is None
        assert BuiltinToolHandler()._jail_root() == ambient_root.resolve()

    def test_contain_workspace_escape_returns_none(self) -> None:
        handler = BuiltinToolHandler()
        result = handler._contain_workspace("/etc")
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self) -> None:
        handler = BuiltinToolHandler()
        result = await handler("nonexistent_tool", {})
        assert "error" in result
        assert "unknown" in result["error"]

    @pytest.mark.asyncio
    async def test_run_project_check_missing_name(self) -> None:
        handler = BuiltinToolHandler()
        result = await handler("run_project_check", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_web_retrieve_missing_url(self) -> None:
        handler = BuiltinToolHandler()
        result = await handler("web_retrieve", {})
        assert "error" in result


class TestRegisterBuiltins:
    def test_is_callable(self) -> None:
        assert callable(register_builtins)
