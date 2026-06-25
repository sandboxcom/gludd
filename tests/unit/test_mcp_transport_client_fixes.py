"""Regression tests for three verified MCP bugs.

Bug 1 (transport.py _readline_with_timeout): oversized JSON-RPC frame
    raises ValueError/LimitOverrunError → subprocess left running.
    Fix: catch both, force-terminate, raise clean MCPTransportError.

Bug 2 (transport.py _validate_package_spec): npx --package pkg@latest
    bypasses the supply-chain pin check on the positional arg.
    Fix: parse --package / -p (and inline --package=) and apply pin check.

Bug 3 (client.py call_tool): get_tool(tool_name) without server_id
    mis-resolves tool names shared across servers (name-only lookup).
    Fix: get_tool(tool_name, server_id=server_id).
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError, _validate_package_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stdio_config(timeout: float = 5.0) -> MCPServerConfig:
    return MCPServerConfig(
        server_id="test-server",
        command=[sys.executable],
        args=["-c", "pass"],
        timeout_seconds=timeout,
    )


def _make_mock_proc(readline_side_effect: list) -> MagicMock:
    """Return a mock asyncio Process whose stdout.readline yields the given sequence."""
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=readline_side_effect)
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


# ---------------------------------------------------------------------------
# Bug 1: oversized frame → clean MCPTransportError + process killed
# ---------------------------------------------------------------------------

class TestOversizedFrameHandling:
    """_readline_with_timeout must surface a clean error and kill the process
    when asyncio raises ValueError or LimitOverrunError for a frame > buffer."""

    @pytest.mark.asyncio
    async def test_value_error_raises_mcp_transport_error_and_kills_process(self) -> None:
        """ValueError from readline (oversized line) → MCPTransportError, proc killed."""
        config = _make_stdio_config()
        client = MCPStdioClient(config)

        proc = _make_mock_proc(readline_side_effect=[ValueError("line too long")])
        # Inject the mock process directly (bypass start()).
        client._process = proc  # type: ignore[attr-defined]

        with pytest.raises(MCPTransportError, match="oversized"):
            await client._readline_with_timeout()

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_limit_overrun_error_raises_mcp_transport_error_and_kills_process(self) -> None:
        """LimitOverrunError from readline (oversized line) → MCPTransportError, proc killed."""
        config = _make_stdio_config()
        client = MCPStdioClient(config)

        proc = _make_mock_proc(
            readline_side_effect=[asyncio.LimitOverrunError("chunk too long", 65536)]
        )
        client._process = proc  # type: ignore[attr-defined]

        with pytest.raises(MCPTransportError, match="oversized"):
            await client._readline_with_timeout()

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_oversized_frame_does_not_leak_subprocess(self) -> None:
        """After an oversized-frame error the process must not be left running."""
        config = _make_stdio_config()
        client = MCPStdioClient(config)

        proc = _make_mock_proc(readline_side_effect=[ValueError("line too long")])
        client._process = proc  # type: ignore[attr-defined]

        with pytest.raises(MCPTransportError):
            await client._readline_with_timeout()

        # kill() is the termination signal; it must have been issued.
        assert proc.kill.call_count >= 1, "subprocess was not killed after oversized frame"

    @pytest.mark.asyncio
    async def test_normal_read_still_works(self) -> None:
        """A normal readline (< buffer limit) must still return the bytes unchanged."""
        config = _make_stdio_config()
        client = MCPStdioClient(config)

        line = b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
        proc = _make_mock_proc(readline_side_effect=[line])
        client._process = proc  # type: ignore[attr-defined]

        result = await client._readline_with_timeout()
        assert result == line


# ---------------------------------------------------------------------------
# Bug 2: --package / -p flag bypass of npx pin check
# ---------------------------------------------------------------------------

class TestNpxPackageFlagPinCheck:
    """_validate_package_spec must check package specs supplied via --package/-p."""

    # --- should PASS (pinned) ---

    def test_positional_pinned_passes(self) -> None:
        """Baseline: positional pinned spec still passes."""
        _validate_package_spec(["npx", "my-tool@1.2.3"], "npx")

    def test_package_flag_space_pinned_passes(self) -> None:
        """npx --package my-tool@1.2.3 (space form) must pass."""
        _validate_package_spec(["npx", "--package", "my-tool@1.2.3", "some-cmd"], "npx")

    def test_short_flag_p_space_pinned_passes(self) -> None:
        """`npx -p my-tool@1.2.3` must pass."""
        _validate_package_spec(["npx", "-p", "my-tool@1.2.3", "some-cmd"], "npx")

    def test_package_flag_equals_pinned_passes(self) -> None:
        """`npx --package=my-tool@1.2.3` inline form must pass."""
        _validate_package_spec(["npx", "--package=my-tool@1.2.3", "some-cmd"], "npx")

    def test_short_flag_equals_pinned_passes(self) -> None:
        """`npx -p=my-tool@1.2.3` inline form must pass."""
        _validate_package_spec(["npx", "-p=my-tool@1.2.3", "some-cmd"], "npx")

    # --- should FAIL (unpinned) ---

    def test_package_flag_latest_tag_rejected(self) -> None:
        """`npx --package pkg@latest` must be rejected as unpinned."""
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["npx", "--package", "pkg@latest", "cmd"], "npx")

    def test_package_flag_bare_name_rejected(self) -> None:
        """`npx --package bare-name` (no version) must be rejected."""
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["npx", "--package", "bare-name", "cmd"], "npx")

    def test_package_flag_range_rejected(self) -> None:
        """`npx --package pkg@^1.0.0` (range) must be rejected."""
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["npx", "--package", "pkg@^1.0.0", "cmd"], "npx")

    def test_short_p_flag_bare_name_rejected(self) -> None:
        """`npx -p bare-name` must be rejected."""
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["npx", "-p", "bare-name", "cmd"], "npx")

    def test_package_flag_equals_latest_rejected(self) -> None:
        """`npx --package=pkg@latest` inline form must be rejected."""
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["npx", "--package=pkg@latest", "cmd"], "npx")

    def test_package_flag_missing_value_rejected(self) -> None:
        """`npx --package` with no following arg must be rejected gracefully."""
        with pytest.raises(MCPTransportError, match="requires a following"):
            _validate_package_spec(["npx", "--package"], "npx")

    def test_package_flag_metachar_in_value_rejected(self) -> None:
        """`npx --package 'evil;rm@1.0.0'` shell meta in spec → rejected."""
        with pytest.raises(MCPTransportError, match="metacharacters"):
            _validate_package_spec(["npx", "--package", "evil;rm@1.0.0", "cmd"], "npx")

    def test_bunx_package_flag_unpinned_rejected(self) -> None:
        """bunx also belongs to _NPM_FAMILY_LAUNCHERS; --package bypass must be blocked."""
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["bunx", "--package", "bare-pkg", "cmd"], "bunx")


# ---------------------------------------------------------------------------
# Bug 3: call_tool must resolve tool per-server, not name-only
# ---------------------------------------------------------------------------

class TestCallToolPerServerResolution:
    """MCPClient.call_tool must use get_tool(name, server_id=...) so that two
    servers advertising the same tool name don't cross-resolve."""

    def _make_client_with_two_servers(
        self,
    ) -> tuple[MCPClient, MagicMock, MagicMock]:
        """Return (client, transport_a, transport_b) with two servers each
        registering a tool named 'do_thing' — bypassing the registry collision
        guard by injecting tools directly into the registry's internal dict."""
        registry = MCPToolRegistry()

        # Bypass register_tool collision guard: inject both tools directly so
        # we can test same-name tools on two servers (the guard itself is
        # intentional; we are testing the client dispatch layer only).
        tool_a = MCPTool(name="do_thing", description="server A version")
        tool_a.server_id = "server-a"
        tool_b = MCPTool(name="do_thing", description="server B version")
        tool_b.server_id = "server-b"
        registry._tools[("server-a", "do_thing")] = tool_a  # type: ignore[attr-defined]
        registry._tools[("server-b", "do_thing")] = tool_b  # type: ignore[attr-defined]

        config_a = MCPServerConfig(server_id="server-a", command=["npx", "srv-a@1.0.0"])
        config_b = MCPServerConfig(server_id="server-b", command=["npx", "srv-b@2.0.0"])
        configs = {"server-a": config_a, "server-b": config_b}

        transport_a = MagicMock()
        transport_a.call_tool = AsyncMock(return_value={"from": "server-a"})
        transport_b = MagicMock()
        transport_b.call_tool = AsyncMock(return_value={"from": "server-b"})

        client = MCPClient(configs, registry)
        client._transports["server-a"] = transport_a  # type: ignore[attr-defined]
        client._transports["server-b"] = transport_b  # type: ignore[attr-defined]

        return client, transport_a, transport_b

    @pytest.mark.asyncio
    async def test_call_tool_routes_to_correct_server(self) -> None:
        """Calling do_thing on server-b must dispatch to transport_b, not transport_a."""
        client, transport_a, transport_b = self._make_client_with_two_servers()

        result = await client.call_tool("server-b", "do_thing", {})

        assert result == {"from": "server-b"}
        transport_b.call_tool.assert_called_once_with("do_thing", {})
        transport_a.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_on_other_server_routes_correctly(self) -> None:
        """Calling do_thing on server-a must dispatch to transport_a."""
        client, transport_a, transport_b = self._make_client_with_two_servers()

        result = await client.call_tool("server-a", "do_thing", {})

        assert result == {"from": "server-a"}
        transport_a.call_tool.assert_called_once_with("do_thing", {})
        transport_b.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool_raises(self) -> None:
        """A tool name not registered to the requested server must raise MCPTransportError."""
        client, _, _ = self._make_client_with_two_servers()

        with pytest.raises(MCPTransportError, match="not registered"):
            await client.call_tool("server-a", "nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_unknown_server_raises(self) -> None:
        """An unrecognised server_id must raise MCPTransportError."""
        client, _, _ = self._make_client_with_two_servers()

        with pytest.raises(MCPTransportError, match="No transport"):
            await client.call_tool("server-x", "do_thing", {})
