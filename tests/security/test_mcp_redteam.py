"""RED-TEAM: security/correctness of the MCP stdio transport + tool-call sink.

Companion findings reproduced here (each test name maps to a finding):

1. HIGH — readline()/drain() had NO timeout: a hung MCP server blocked the
   agent forever. ``timeout_seconds`` (config, default 30) was dead code.
2. HIGH — ``env = {**os.environ, **config.env}`` handed the FULL host
   environment (ANTHROPIC_API_KEY, GLUDD_AUTH_PSK, cloud creds) to every MCP
   subprocess. A malicious/compromised server could exfiltrate them.
3. HIGH (latent) — model-chosen tool name + args went straight to
   ``call_tool(None, ...)`` with no allowlist: the model could name ANY tool
   and reach an unadvertised/unintended server. No capability gate.
4. MED — ``stop()`` did terminate() then an UNBOUNDED wait(): a process that
   ignores SIGTERM hangs shutdown forever.
6. MED — ``start_all`` leaked an already-started transport's subprocess if a
   later step in its start raised (it was never tracked, so stop_all couldn't
   reap it).

asyncio_mode=auto (pyproject) — plain ``async def test_*`` are collected.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.execution.tool_loop import ToolCallLoop
from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


def _make_config(**overrides: object) -> MCPServerConfig:
    defaults: dict = {
        "server_id": "test-server",
        "command": ["python", "-m", "some_mcp_server"],
        "args": [],
        "env": {"FOO": "bar"},
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _init_response() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
    }


def _mock_process(responses: list[dict]) -> MagicMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(
        side_effect=[(json.dumps(r) + "\n").encode() for r in responses]
    )
    proc.stderr = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


# ---------------------------------------------------------------------------
# Finding 1 — readline()/drain() timeout
# ---------------------------------------------------------------------------
class TestFinding1HungServerTimeout:
    async def test_hung_readline_times_out_and_kills(self):
        config = _make_config(timeout_seconds=0.05)
        proc = _mock_process([_init_response()])

        async def _hang():
            await asyncio.sleep(3600)
            return b""

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()

        proc.stdout.readline = AsyncMock(side_effect=_hang)

        # Bound the whole call: if the fix regressed (no inner timeout), this
        # outer guard fires and the test fails loudly instead of hanging CI.
        with pytest.raises(MCPTransportError, match="timed out"):
            await asyncio.wait_for(client.call_tool("x", {}), timeout=5)
        proc.kill.assert_called_once()

    async def test_hung_drain_times_out(self):
        config = _make_config(timeout_seconds=0.05)
        proc = _mock_process([_init_response()])

        async def _hang_drain():
            await asyncio.sleep(3600)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()

        proc.stdin.drain = AsyncMock(side_effect=_hang_drain)

        with pytest.raises(MCPTransportError, match="timed out"):
            await asyncio.wait_for(client.call_tool("x", {}), timeout=5)


# ---------------------------------------------------------------------------
# Finding 2 — host-env isolation
# ---------------------------------------------------------------------------
class TestFinding2EnvIsolation:
    async def test_full_host_env_not_leaked_to_subprocess(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak-me")
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk-leak-me")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-leak-me")
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("HOME", "/home/agent")

        config = _make_config(env={"SERVER_FLAG": "1"})
        proc = _mock_process([_init_response()])

        # The launch-command allowlist/PATH-resolution guard (Finding #65) is
        # a separate concern with its own coverage; here we deliberately narrow
        # PATH to assert env isolation, which would otherwise make the bare
        # ``python`` exec unresolvable. Neutralise just that guard so this test
        # exercises env assembly only.
        with (
            patch("general_ludd.mcp.transport._validate_launch_command"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mexec,
        ):
            client = MCPStdioClient(config)
            await client.start()

        env = mexec.call_args.kwargs["env"]
        # Allowlisted hygiene vars + declared server env survive.
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/agent"
        assert env["SERVER_FLAG"] == "1"
        # Secrets are categorically absent.
        for leaked in ("ANTHROPIC_API_KEY", "GLUDD_AUTH_PSK", "AWS_SECRET_ACCESS_KEY"):
            assert leaked not in env, f"{leaked} leaked to MCP subprocess"

    async def test_declared_env_can_override_allowlist(self, monkeypatch):
        # A server may legitimately set its own PATH; the declared value wins.
        monkeypatch.setenv("PATH", "/usr/bin")
        config = _make_config(env={"PATH": "/custom/bin"})
        proc = _mock_process([_init_response()])
        # See note in test_full_host_env_not_leaked_to_subprocess: the launch
        # guard is out of scope for this env-isolation assertion.
        with (
            patch("general_ludd.mcp.transport._validate_launch_command"),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mexec,
        ):
            await MCPStdioClient(config).start()
        assert mexec.call_args.kwargs["env"]["PATH"] == "/custom/bin"


# ---------------------------------------------------------------------------
# Finding 3 — capability gate between model output and the MCP sink
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def _tc(name: str, args: dict) -> dict:
    return {
        "id": "call_1",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class TestFinding3CapabilityGate:
    def _job(self):
        job = MagicMock()
        job.job_id = "JOB-1"
        return job

    async def test_unregistered_tool_is_rejected_and_never_reaches_sink(self):
        registry = MCPToolRegistry()
        registry.register_tool("fs", MCPTool(name="read_file", server_id="fs"))

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock(
            return_value=[MCPTool(name="read_file", server_id="fs")]
        )
        mcp_client.call_tool = AsyncMock(return_value={"ok": True})

        gateway = MagicMock()
        # First turn: model invents an unadvertised tool. Second turn: it stops.
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("rm_rf_root", {"path": "/"})]),
                _Resp(content="done"),
            ]
        )

        loop = ToolCallLoop(gateway, mcp_client=mcp_client, mcp_registry=registry)
        await loop.run_with_tools(self._job(), "sys", "user")

        # The forged tool name NEVER reached the MCP sink — capability gate held.
        mcp_client.call_tool.assert_not_awaited()

    async def test_registered_tool_resolves_real_server_id_not_none(self):
        registry = MCPToolRegistry()
        registry.register_tool("gitsrv", MCPTool(name="git_status", server_id="gitsrv"))

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock(
            return_value=[MCPTool(name="git_status", server_id="gitsrv")]
        )
        mcp_client.call_tool = AsyncMock(return_value={"ok": True})

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("git_status", {})]),
                _Resp(content="done"),
            ]
        )

        loop = ToolCallLoop(gateway, mcp_client=mcp_client, mcp_registry=registry)
        await loop.run_with_tools(self._job(), "sys", "user")

        # server_id is the resolved real id — NOT the old wildcard None.
        mcp_client.call_tool.assert_awaited_once()
        called_server_id = mcp_client.call_tool.await_args.args[0]
        assert called_server_id == "gitsrv"

    async def test_gate_active_via_client_registry_fallback(self):
        # No explicit mcp_registry passed — the loop falls back to the client's
        # own _registry so the gate is on by default.
        registry = MCPToolRegistry()  # empty: nothing is advertised
        mcp_client = MagicMock()
        mcp_client._registry = registry
        mcp_client.list_tools = AsyncMock(return_value=[])
        mcp_client.call_tool = AsyncMock(return_value={"ok": True})

        gateway = MagicMock()
        gateway.call_model = MagicMock(
            side_effect=[
                _Resp(tool_calls=[_tc("anything", {})]),
                _Resp(content="done"),
            ]
        )

        loop = ToolCallLoop(gateway, mcp_client=mcp_client)
        await loop.run_with_tools(self._job(), "sys", "user")
        mcp_client.call_tool.assert_not_awaited()


# ---------------------------------------------------------------------------
# Finding 4 — bounded stop() with kill fallback
# ---------------------------------------------------------------------------
class TestFinding4StopKillFallback:
    async def test_stop_escalates_to_kill_on_unresponsive_terminate(self):
        config = _make_config(timeout_seconds=0.05)
        proc = _mock_process([_init_response()])

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            client = MCPStdioClient(config)
            await client.start()

        async def _hang():
            await asyncio.sleep(3600)
            return 0

        proc.wait = AsyncMock(side_effect=_hang)

        # If the fix regressed, the unbounded wait() would hang here — the outer
        # wait_for makes that a visible failure rather than a frozen suite.
        await asyncio.wait_for(client.stop(), timeout=5)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Finding 6 — no subprocess leak when start_all fails mid-loop
# ---------------------------------------------------------------------------
class TestFinding6NoTransportLeakOnStartFailure:
    async def test_failed_start_stops_the_transport(self):
        configs = {
            "a": _make_config(server_id="a", command=["a"]),
        }
        registry = MCPToolRegistry()

        transport = MagicMock()
        transport.start = AsyncMock()
        transport.list_tools = AsyncMock(side_effect=RuntimeError("list boom"))
        transport.stop = AsyncMock()

        with patch("general_ludd.mcp.client.MCPStdioClient", return_value=transport):
            client = MCPClient(configs, registry)
            with pytest.raises(RuntimeError, match="list boom"):
                await client.start_all()

        # The transport whose start sequence failed was reaped, not leaked.
        transport.stop.assert_awaited_once()
        assert client._transports == {}
        assert client._started_pids == []
