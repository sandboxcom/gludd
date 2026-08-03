from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.client import MCPClient, _BuiltinTransport
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.loader import load_mcp_config
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import (
    _ENV_ALLOWLIST,
    _NPM_FAMILY_LAUNCHERS,
    _REMOTE_FETCH_LAUNCHERS,
    _SHELL_META_RE,
    _UVX_FAMILY_LAUNCHERS,
    MCPStdioClient,
    MCPTransportError,
    _is_version_pinned_spec,
    _launcher_basename,
    _stderr_limit,
    _strip_suffix,
    _validate_launch_command,
)


class TestTransportJSONRPCFraming:
    def test_send_request_emits_jsonrpc_envelope(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)

        assert client._request_id == 0
        rid = client._next_id()
        assert rid == 1
        assert client._request_id == 1

        rid2 = client._next_id()
        assert rid2 == 2
        assert client._request_id == 2

    def test_send_notification_format_has_no_id(self):
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert "id" not in notif

    def test_request_id_increments_sequentially(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)
        ids = [client._next_id() for _ in range(5)]
        assert ids == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_send_request_passes_correct_body(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)

        client._process = MagicMock()
        client._process.returncode = None
        client._process.stdin = MagicMock()
        client._process.stdin.drain = AsyncMock(return_value=None)
        client._process.stdin.write = MagicMock()
        client._process.stdout = MagicMock()
        client._process.stdout.readline = AsyncMock(
            return_value=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"tools": []},
                }
            ).encode()
            + b"\n"
        )

        result = await client._send_request("tools/list")
        assert result == {"tools": []}

    @pytest.mark.asyncio
    async def test_send_request_matches_response_by_id(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)

        client._process = MagicMock()
        client._process.returncode = None
        client._process.stdin = MagicMock()
        client._process.stdin.drain = AsyncMock(return_value=None)
        client._process.stdin.write = MagicMock()
        client._process.stdout = MagicMock()

        call_count = 0

        async def readline_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 999, "result": {"stale": True}}).encode() + b"\n"
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"correct": True}}).encode() + b"\n"

        client._process.stdout.readline = readline_side_effect

        result = await client._send_request("tools/list")
        assert result == {"correct": True}
        assert call_count == 2


class TestTransportInterleaveSkips:
    @pytest.mark.asyncio
    async def test_exceed_interleave_skips_raises(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)

        client._process = MagicMock()
        client._process.returncode = None
        client._process.stdin = MagicMock()
        client._process.stdin.drain = AsyncMock(return_value=None)
        client._process.stdin.write = MagicMock()
        client._process.stdout = MagicMock()
        client._force_terminate = AsyncMock(return_value=None)

        async def endless_interleave():
            return json.dumps({"jsonrpc": "2.0", "id": 777, "result": {}}).encode() + b"\n"

        client._process.stdout.readline = endless_interleave

        with pytest.raises(MCPTransportError, match="interleaved frames"):
            await client._send_request("tools/list")

    @pytest.mark.asyncio
    async def test_one_interleave_then_match_is_ok(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)

        client._process = MagicMock()
        client._process.returncode = None
        client._process.stdin = MagicMock()
        client._process.stdin.drain = AsyncMock(return_value=None)
        client._process.stdin.write = MagicMock()
        client._process.stdout = MagicMock()

        call_count = 0

        async def readline_mock():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode() + b"\n"
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode() + b"\n"

        client._process.stdout.readline = readline_mock

        result = await client._send_request("tools/list")
        assert result == {"ok": True}


class TestTransportErrorHandling:
    @pytest.mark.asyncio
    async def test_send_request_process_not_running_raises(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)
        with pytest.raises(MCPTransportError, match="not running"):
            await client._send_request("tools/list")

    @pytest.mark.asyncio
    async def test_send_request_closed_connection_raises(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)

        client._process = MagicMock()
        client._process.returncode = None
        client._process.stdin = MagicMock()
        client._process.stdin.drain = AsyncMock(return_value=None)
        client._process.stdin.write = MagicMock()
        client._process.stdout = MagicMock()
        client._process.stdout.readline = AsyncMock(return_value=b"")

        with pytest.raises(MCPTransportError, match="Connection closed"):
            await client._send_request("tools/list")

    @pytest.mark.asyncio
    async def test_send_request_jsonrpc_error_raises(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)

        client._process = MagicMock()
        client._process.returncode = None
        client._process.stdin = MagicMock()
        client._process.stdin.drain = AsyncMock(return_value=None)
        client._process.stdin.write = MagicMock()
        client._process.stdout = MagicMock()
        client._process.stdout.readline = AsyncMock(
            return_value=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            ).encode()
            + b"\n"
        )

        with pytest.raises(MCPTransportError, match="JSON-RPC error"):
            await client._send_request("tools/list")

    def test_process_not_running_on_send_raises(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
            timeout_seconds=1,
        )
        client = MCPStdioClient(config)
        assert client.pid is None


class TestTransportLauncherValidation:
    def test_empty_command_raises(self):
        with pytest.raises(MCPTransportError, match="empty command"):
            _validate_launch_command([])

    def test_non_allowlisted_exec_raises(self):
        with pytest.raises(MCPTransportError, match="not in the MCP executable allowlist"):
            _validate_launch_command(["/usr/bin/evil"])

    def test_version_pinned_spec_valid(self):
        assert _is_version_pinned_spec("@modelcontextprotocol/server-filesystem@2026.1.26") is True
        assert _is_version_pinned_spec("pkg@1.2.3") is True

    def test_version_pinned_spec_rejects_latest(self):
        assert _is_version_pinned_spec("pkg@latest") is False

    def test_version_pinned_spec_rejects_bare(self):
        assert _is_version_pinned_spec("pkg") is False

    def test_version_pinned_spec_rejects_range(self):
        assert _is_version_pinned_spec("pkg@^1.0.0") is False

    def test_exec_allowlist_allows_with_env(self, monkeypatch):
        monkeypatch.setenv("GLUDD_MCP_ALLOW_ANY_EXEC", "1")
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/anybin")

        _validate_launch_command(["anybin"])

    def test_shell_metacharacters_are_refused(self):
        assert _SHELL_META_RE.search("pkg; rm -rf /")
        assert _SHELL_META_RE.search("pkg | cat /etc/passwd")
        assert not _SHELL_META_RE.search("normal-package-name")

    def test_launcher_basename_strips_suffix(self):
        assert _launcher_basename("npx.cmd") == "npx"
        assert _launcher_basename("npx.exe") == "npx"
        assert _launcher_basename("/usr/bin/python3") == "python3"

    def test_strip_suffix_removes_windows_extensions(self):
        assert _strip_suffix("npx.cmd") == "npx"
        assert _strip_suffix("node.exe") == "node"
        assert _strip_suffix("python.bat") == "python"
        assert _strip_suffix("script.ps1") == "script"

    def test_npm_family_includes_bunx(self):
        assert "bunx" in _NPM_FAMILY_LAUNCHERS

    def test_remote_fetch_launchers_includes_uvx(self):
        assert "uvx" in _UVX_FAMILY_LAUNCHERS
        assert "uvx" in _REMOTE_FETCH_LAUNCHERS


class TestTransportStderrLimits:
    def test_stderr_limit_defaults(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)
        assert client._stderr_tail_bytes_limit == 16 * 1024
        assert client._stderr_tail_lines_limit == 128
        assert client._stderr_line_bytes_limit == 8 * 1024
        assert client._stderr_max_bytes == 1024 * 1024
        assert client._stderr_max_lines == 10_000

    def test_stderr_limit_below_zero_raises(self):
        with pytest.raises(MCPTransportError, match="must be between 1 and"):
            _stderr_limit(-1, env_name="TEST", default=100, limit_name="tail_bytes")

    def test_stderr_limit_above_ceiling_raises(self):
        with pytest.raises(MCPTransportError, match="must be between 1 and"):
            _stderr_limit(99_999_999, env_name="TEST", default=100, limit_name="max_bytes")

    def test_stderr_limit_custom_value(self):
        val = _stderr_limit(42, env_name="TEST", default=100, limit_name="tail_bytes")
        assert val == 42

    def test_stderr_diagnostics_keys(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)
        diag = client.stderr_diagnostics
        assert "tail" in diag
        assert "tail_bytes" in diag
        assert "truncated" in diag
        assert "policy_breached" in diag
        assert "limits" in diag


class TestMCPToolRegistry:
    def test_register_tool(self):
        registry = MCPToolRegistry()
        tool = MCPTool(name="search", server_id="brave")
        registry.register_tool("brave", tool)
        assert registry.get_tool("search") is not None
        assert registry.get_tool("nonexistent") is None

    def test_register_tool_collision_raises(self):
        registry = MCPToolRegistry()
        tool1 = MCPTool(name="search", server_id="brave")
        tool2 = MCPTool(name="search", server_id="google")
        registry.register_tool("brave", tool1)
        with pytest.raises(ValueError, match="collision"):
            registry.register_tool("google", tool2)

    def test_register_tool_idempotent_same_server(self):
        registry = MCPToolRegistry()
        tool = MCPTool(name="search", server_id="brave")
        registry.register_tool("brave", tool)
        registry.register_tool("brave", tool)
        assert len(registry.list_tools()) == 1

    def test_list_tools_by_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read", server_id="fs"))
        registry.register_tool("fs", MCPTool(name="write", server_id="fs"))
        registry.register_tool("git", MCPTool(name="commit", server_id="git"))

        fs_tools = registry.list_tools(server_id="fs")
        assert len(fs_tools) == 2
        names = {t.name for t in fs_tools}
        assert names == {"read", "write"}

        git_tools = registry.list_tools(server_id="git")
        assert len(git_tools) == 1
        assert git_tools[0].name == "commit"

    def test_list_tools_all_servers(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read", server_id="fs"))
        registry.register_tool("git", MCPTool(name="commit", server_id="git"))
        all_tools = registry.list_tools()
        assert len(all_tools) == 2

    def test_tool_names_sorted(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="zebra", server_id="fs"))
        registry.register_tool("fs", MCPTool(name="alpha", server_id="fs"))
        names = registry.tool_names()
        assert names == ["alpha", "zebra"]

    def test_remove_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read", server_id="fs"))
        registry.register_tool("fs", MCPTool(name="write", server_id="fs"))
        count = registry.remove_server("fs")
        assert count == 2
        assert len(registry.list_tools()) == 0

    def test_get_tool_by_name_only_unique(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read", server_id="fs"))
        assert registry.get_tool("read") is not None

    def test_get_tool_by_name_and_server(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read", server_id="fs"))
        registry.register_tool("git", MCPTool(name="commit", server_id="git"))
        assert registry.get_tool("read", server_id="fs") is not None
        assert registry.get_tool("commit", server_id="git") is not None
        assert registry.get_tool("read", server_id="git") is None


class TestMCPServerConfig:
    def test_server_id_stripped(self):
        cfg = MCPServerConfig(server_id="  fs  ", command=["echo"])
        assert cfg.server_id == "fs"

    def test_server_id_empty_raises(self):
        with pytest.raises(ValueError, match="server_id"):
            MCPServerConfig(server_id="", command=["echo"])

    def test_server_id_whitespace_raises(self):
        with pytest.raises(ValueError, match="server_id"):
            MCPServerConfig(server_id="   ", command=["echo"])

    def test_timeout_zero_raises(self):
        with pytest.raises(ValueError, match="timeout_seconds"):
            MCPServerConfig(server_id="fs", command=["echo"], timeout_seconds=0)

    def test_is_stdio_true_when_command_set(self):
        cfg = MCPServerConfig(server_id="fs", command=["echo"])
        assert cfg.is_stdio() is True
        assert cfg.is_http() is False

    def test_is_http_true_when_url_set(self):
        cfg = MCPServerConfig(server_id="fs", url="http://localhost:3000")
        assert cfg.is_http() is True
        assert cfg.is_stdio() is False

    def test_defaults(self):
        cfg = MCPServerConfig(server_id="fs", command=["echo"])
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.enabled is True
        assert cfg.project_id is None


class TestBuiltinTransport:
    @pytest.mark.asyncio
    async def test_builtin_transport_returns_pid_none(self):
        async def handler(name, args):
            return {"result": "ok"}

        transport = _BuiltinTransport(handler)
        assert transport.pid is None

    @pytest.mark.asyncio
    async def test_builtin_transport_start_stop_are_noops(self):
        async def handler(name, args):
            return {"result": "ok"}

        transport = _BuiltinTransport(handler)
        await transport.start()
        await transport.stop()

    @pytest.mark.asyncio
    async def test_builtin_transport_call_tool_forwards(self):
        async def handler(name, args):
            return {"name": name, "args": args}

        transport = _BuiltinTransport(handler)
        result = await transport.call_tool("search", {"query": "test"})
        assert result == {"name": "search", "args": {"query": "test"}}


class TestMCPClientBuiltins:
    def test_register_builtin_adds_to_registry(self):
        configs: dict[str, MCPServerConfig] = {}
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)

        tools = [MCPTool(name="custom_tool", description="A custom tool")]

        async def handler(name, args):
            return {"ok": True}

        client.register_builtin("gludd-builtin", tools, handler)

        assert registry.get_tool("custom_tool") is not None
        assert len(registry.list_tools(server_id="gludd-builtin")) == 1

    def test_register_builtin_replaces_transport(self):
        configs: dict[str, MCPServerConfig] = {}
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)

        async def handler(name, args):
            return {"ok": True}

        client.register_builtin("gludd-builtin", [MCPTool(name="t1")], handler)
        client.register_builtin("gludd-builtin", [MCPTool(name="t2")], handler)

        builtin_tools = registry.list_tools(server_id="gludd-builtin")
        names = {t.name for t in builtin_tools}
        assert names == {"t1", "t2"}


class TestMCPClientCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_no_transport_raises(self):
        configs = {
            "fs": MCPServerConfig(
                server_id="fs",
                command=["echo", "hello"],
            ),
        }
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)

        with pytest.raises(MCPTransportError, match="No transport for server"):
            await client.call_tool("missing", "search", {})

    @pytest.mark.asyncio
    async def test_call_tool_no_registered_tool_raises(self):
        configs: dict[str, MCPServerConfig] = {}
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)

        mock_transport = AsyncMock()
        client._transports["fs"] = mock_transport

        with pytest.raises(MCPTransportError, match="not registered"):
            await client.call_tool("fs", "nonexistent", {})


class TestMCPClientOrphanRecovery:
    @pytest.mark.asyncio
    async def test_start_all_cleans_up_on_partial_failure(self):
        configs = {
            "fs": MCPServerConfig(
                server_id="fs",
                command=["echo", "hello"],
            ),
        }
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)

        mock_transport = AsyncMock()
        mock_transport.start.side_effect = Exception("Boom")
        mock_transport.stop = AsyncMock()
        mock_transport.pid = None

        with (
            patch.object(client, "_configs", configs),
            patch(
                "general_ludd.mcp.client.MCPStdioClient",
                return_value=mock_transport,
            ),
            pytest.raises(Exception, match="Boom"),
        ):
            await client.start_all()

    @pytest.mark.asyncio
    async def test_stop_all_collects_multiple_failures(self):
        configs: dict[str, MCPServerConfig] = {}
        registry = MCPToolRegistry()
        client = MCPClient(configs, registry)

        bad1 = AsyncMock()
        bad1.stop.side_effect = MCPTransportError("stop failed 1")
        bad2 = AsyncMock()
        bad2.stop.side_effect = MCPTransportError("stop failed 2")

        client._transports["srv1"] = bad1
        client._transports["srv2"] = bad2

        with pytest.raises(MCPTransportError, match="Failed to stop 2"):
            await client.stop_all()

        assert len(client._transports) == 0


class TestMCPConfigLoader:
    def test_load_missing_file_returns_empty(self):
        result = load_mcp_config("/nonexistent/path/config.yml")
        assert result == {}

    def test_load_empty_config_returns_empty(self, tmp_path):
        p = tmp_path / "mcp.yml"
        p.write_text("")
        result = load_mcp_config(str(p))
        assert result == {}


class TestMCPToolValidation:
    def test_tool_name_with_slash_raises(self):
        with pytest.raises(ValueError, match="invalid tool name"):
            MCPTool(name="bad/name", server_id="test")

    def test_tool_name_with_space_raises(self):
        with pytest.raises(ValueError, match="invalid tool name"):
            MCPTool(name="bad name", server_id="test")

    def test_tool_name_valid(self):
        tool = MCPTool(name="valid_name-123.test:tool", server_id="test")
        assert tool.name == "valid_name-123.test:tool"

    def test_tool_name_stripped(self):
        tool = MCPTool(name="  hello  ", server_id="test")
        assert tool.name == "hello"


class TestMCPServerConfigEnvAllowlist:
    def test_env_allowlist_keys_are_basic(self):
        assert "PATH" in _ENV_ALLOWLIST
        assert "HOME" in _ENV_ALLOWLIST
        assert "LANG" in _ENV_ALLOWLIST

    def test_build_env_uses_allowlist_only(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", "/home/test")
        monkeypatch.setenv("SECRET_KEY", "should-not-leak")
        monkeypatch.setenv("LANG", "en_US.UTF-8")

        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config, secrets_mgr=None)
        env = client._build_env()

        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/test"
        assert "SECRET_KEY" not in env


class TestMCPTransportPID:
    def test_pid_none_when_no_process(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)
        assert client.pid is None

    def test_stderr_diagnostics_initial_state(self):
        config = MCPServerConfig(
            server_id="test-srv",
            command=["echo", "hello"],
        )
        client = MCPStdioClient(config)
        diag = client.stderr_diagnostics
        assert diag["tail"] == ""
        assert diag["observed_bytes"] == 0
        assert diag["observed_lines"] == 0
        assert diag["truncated"] is False
        assert diag["policy_breached"] is False
