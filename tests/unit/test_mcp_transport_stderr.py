"""D-24 regression coverage for bounded MCP stderr diagnostics."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


@pytest.fixture()
def stdio_config() -> MCPServerConfig:
    return MCPServerConfig(
        server_id="test-server",
        command=[sys.executable],
        args=["-m", "json.tool"],
        env={"MCP_TOKEN": "diagnostic-secret-value"},
        timeout_seconds=0.2,
    )


def _process(stderr_chunks: list[bytes]) -> MagicMock:
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(
        return_value=(
            b'{"jsonrpc":"2.0","id":1,"result":'
            b'{"protocolVersion":"2024-11-05","capabilities":{}}}\n'
        )
    )
    proc.stderr = MagicMock()
    proc.stderr.read = AsyncMock(side_effect=stderr_chunks)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    return proc


async def _await_stderr(client: MCPStdioClient) -> None:
    task = client._stderr_task
    assert task is not None
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_stderr_is_concurrently_drained_redacted_and_bounded(
    stdio_config: MCPServerConfig,
) -> None:
    proc = _process(
        [
            b"discarded\nkept-one\n",
            b"password=diagnostic-secret-value\nkept-two\n",
            b"",
        ]
    )

    with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
        client = MCPStdioClient(
            stdio_config,
            stderr_tail_bytes=80,
            stderr_tail_lines=2,
        )
        await client.start()
        await _await_stderr(client)

    assert spawn.call_args.kwargs["stderr"] == asyncio.subprocess.PIPE
    diagnostics = client.stderr_diagnostics
    assert len(diagnostics["tail"].encode()) <= 80
    assert diagnostics["tail_lines"] <= 2
    assert "diagnostic-secret-value" not in diagnostics["tail"]
    assert "REDACTED" in diagnostics["tail"]
    assert diagnostics["truncated"] is True
    assert diagnostics["truncated_lines"] >= 2
    assert diagnostics["observed_lines"] == 4


@pytest.mark.asyncio
async def test_infinite_stderr_is_cancelled_at_total_byte_policy_limit(
    stdio_config: MCPServerConfig,
) -> None:
    proc = _process([])

    async def endless_stderr(_size: int) -> bytes:
        return b"noise-line\n"

    proc.stderr.read = AsyncMock(side_effect=endless_stderr)

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        client = MCPStdioClient(
            stdio_config,
            stderr_tail_bytes=24,
            stderr_tail_lines=2,
            stderr_max_bytes=32,
        )
        await client.start()
        await _await_stderr(client)

    diagnostics = client.stderr_diagnostics
    proc.kill.assert_called_once()
    assert proc.stderr.read.await_count <= 4
    assert diagnostics["policy_breached"] is True
    assert diagnostics["policy_reason"] == "max_bytes"
    assert diagnostics["observed_bytes"] <= 33
    assert len(diagnostics["tail"].encode()) <= 24
    with pytest.raises(MCPTransportError, match=r"stderr policy breach.*max_bytes"):
        await client.list_tools()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "chunks", "reason"),
    [
        ({"stderr_line_bytes": 4}, [b"12345", b""], "line_bytes"),
        ({"stderr_max_lines": 2}, [b"one\ntwo\nthree\n", b""], "max_lines"),
    ],
)
async def test_line_policies_cancel_without_retaining_offending_payload(
    stdio_config: MCPServerConfig,
    kwargs: dict[str, int],
    chunks: list[bytes],
    reason: str,
) -> None:
    proc = _process(chunks)

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        client = MCPStdioClient(stdio_config, **kwargs)
        await client.start()
        await _await_stderr(client)

    diagnostics = client.stderr_diagnostics
    proc.kill.assert_called_once()
    assert diagnostics["policy_breached"] is True
    assert diagnostics["policy_reason"] == reason
    assert "12345" not in diagnostics["tail"]
    assert "three" not in diagnostics["tail"]


def test_stderr_limits_are_configurable_from_environment(
    stdio_config: MCPServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_MCP_STDERR_TAIL_BYTES", "123")
    monkeypatch.setenv("GLUDD_MCP_STDERR_MAX_LINES", "7")

    client = MCPStdioClient(stdio_config)

    diagnostics = client.stderr_diagnostics
    assert diagnostics["limits"]["tail_bytes"] == 123
    assert diagnostics["limits"]["max_lines"] == 7


def test_invalid_stderr_limit_fails_closed(
    stdio_config: MCPServerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_MCP_STDERR_MAX_BYTES", "unbounded")

    with pytest.raises(MCPTransportError, match="GLUDD_MCP_STDERR_MAX_BYTES"):
        MCPStdioClient(stdio_config)
