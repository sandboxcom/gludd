"""Slice 2: run_project_check exposed as a model-callable builtin MCP tool.

The agent has no first-party tool mechanism — every model-callable tool flows
through the MCP client path. These tests exercise the synthetic ``gludd-builtin``
in-process server (``MCPClient.register_builtin`` + ``mcp.builtins``) that makes
``run_project_check`` reachable via ``MCPClient.call_tool`` without any subprocess
MCP server or network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.mcp.builtins import (
    BUILTIN_SERVER_ID,
    RUN_PROJECT_CHECK_TOOL,
    register_builtins,
)
from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry


def _make_client() -> MCPClient:
    """MCPClient with no external servers — only the builtin will be present."""
    return MCPClient(configs={}, registry=MCPToolRegistry())


def _write_true_project(tmp_path: Path) -> Path:
    """A minimal project.yml whose 'test' check runs the always-passing `true`."""
    (tmp_path / "project.yml").write_text(
        'name: fixture\n'
        'allowed_exec: ["true"]\n'
        'commands:\n'
        '  test: "true"\n',
        encoding="utf-8",
    )
    return tmp_path


class TestBuiltinRegistration:
    @pytest.mark.asyncio
    async def test_builtin_tool_appears_in_list_tools(self) -> None:
        client = _make_client()
        register_builtins(client)

        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert RUN_PROJECT_CHECK_TOOL.name in names

    @pytest.mark.asyncio
    async def test_builtin_tool_scoped_to_builtin_server(self) -> None:
        client = _make_client()
        register_builtins(client)

        scoped = await client.list_tools(BUILTIN_SERVER_ID)
        assert set(t.name for t in scoped) == {
            "run_project_check",
            "web_retrieve",
            "web_fetch",
            "web_fetch_parsed",
            "web_search",
            "web_crawl",
            "web_render",
        }
        # server_id is stamped by the registry on registration.
        assert scoped[0].server_id == BUILTIN_SERVER_ID

    def test_run_project_check_tool_schema(self) -> None:
        schema = RUN_PROJECT_CHECK_TOOL.input_schema
        assert schema["required"] == ["check_name"]
        assert "check_name" in schema["properties"]
        assert "workspace" in schema["properties"]


class TestRegisterBuiltinMechanism:
    @pytest.mark.asyncio
    async def test_register_builtin_dispatches_to_handler(self) -> None:
        """The synthetic transport forwards call_tool -> the coroutine handler."""
        seen: dict[str, object] = {}

        async def handler(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
            seen["tool"] = tool_name
            seen["args"] = arguments
            return {"ok": True}

        client = _make_client()
        client.register_builtin("srv", [MCPTool(name="ping")], handler)

        result = await client.call_tool("srv", "ping", {"x": 1})
        assert result == {"ok": True}
        assert seen == {"tool": "ping", "args": {"x": 1}}

    @pytest.mark.asyncio
    async def test_builtin_transport_stop_all_is_noop_safe(self) -> None:
        """stop_all must not raise on the synthetic transport (no subprocess)."""
        async def handler(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
            return {}

        client = _make_client()
        client.register_builtin("srv", [MCPTool(name="ping")], handler)
        await client.stop_all()  # must not raise
        assert client._transports == {}


class TestRunProjectCheckDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_returns_passed_check_result(self, tmp_path: Path) -> None:
        _write_true_project(tmp_path)
        client = _make_client()
        # tmp_path is the jail root here (the agent workspace), so a model-supplied
        # workspace equal to it is contained and allowed.
        register_builtins(client, default_workspace=str(tmp_path))

        result = await client.call_tool(
            BUILTIN_SERVER_ID,
            "run_project_check",
            {"check_name": "test", "workspace": str(tmp_path)},
        )

        assert result["name"] == "test"
        assert result["passed"] is True
        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_dispatch_uses_default_workspace(self, tmp_path: Path) -> None:
        _write_true_project(tmp_path)
        client = _make_client()
        register_builtins(client, default_workspace=str(tmp_path))

        result = await client.call_tool(
            BUILTIN_SERVER_ID, "run_project_check", {"check_name": "test"},
        )
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_missing_project_yml_is_failsoft(self, tmp_path: Path) -> None:
        # No project.yml written → ProjectProfileError → error dict, not raise.
        client = _make_client()
        register_builtins(client, default_workspace=str(tmp_path))

        result = await client.call_tool(
            BUILTIN_SERVER_ID, "run_project_check", {"check_name": "test"},
        )
        assert "error" in result
        assert result["check"] == "test"

    @pytest.mark.asyncio
    async def test_missing_check_name_is_failsoft(self, tmp_path: Path) -> None:
        _write_true_project(tmp_path)
        client = _make_client()
        register_builtins(client, default_workspace=str(tmp_path))

        result = await client.call_tool(
            BUILTIN_SERVER_ID, "run_project_check", {},
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_undeclared_check_is_failsoft(self, tmp_path: Path) -> None:
        _write_true_project(tmp_path)
        client = _make_client()
        register_builtins(client, default_workspace=str(tmp_path))

        result = await client.call_tool(
            BUILTIN_SERVER_ID,
            "run_project_check",
            {"check_name": "nonexistent", "workspace": str(tmp_path)},
        )
        assert "error" in result
        assert result["check"] == "nonexistent"

    @pytest.mark.asyncio
    async def test_workspace_escaping_jail_is_refused(self, tmp_path: Path) -> None:
        # SECURITY (path-escape guard): the jail root is `jail` (the agent
        # workspace). A model-supplied workspace pointing OUTSIDE it — even a
        # sibling dir with its own valid project.yml — must be refused fail-closed
        # and never executed. The in-jail workspace stays allowed.
        jail = tmp_path / "workspace"
        jail.mkdir()
        _write_true_project(jail)
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        _write_true_project(outside)  # a real project.yml on a forbidden path

        client = _make_client()
        register_builtins(client, default_workspace=str(jail))

        refused = await client.call_tool(
            BUILTIN_SERVER_ID,
            "run_project_check",
            {"check_name": "test", "workspace": str(outside)},
        )
        assert "error" in refused
        assert "escape" in refused["error"].lower()
        assert refused["check"] == "test"
        # 'passed' must NOT be present — the forbidden project.yml never ran.
        assert "passed" not in refused

        allowed = await client.call_tool(
            BUILTIN_SERVER_ID,
            "run_project_check",
            {"check_name": "test", "workspace": str(jail)},
        )
        assert allowed["passed"] is True
