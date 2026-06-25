"""Regression test: MCPStdioClient must NOT use stderr=PIPE.

A subprocess with stderr=PIPE and an undrained stderr pipe will deadlock once
the server writes more than ~64 KB to stderr (the OS pipe buffer fills and the
write(2) call blocks, which in turn blocks every subsequent tool call).  The
fix is stderr=DEVNULL.  This test ensures the value never silently regresses
back to PIPE.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import MCPStdioClient


@pytest.fixture()
def stdio_config() -> MCPServerConfig:
    """Minimal config that passes _validate_launch_command for 'python'."""
    return MCPServerConfig(
        server_id="test-server",
        command=[sys.executable],
        args=["-c", "import time; time.sleep(0)"],
        timeout_seconds=5.0,
    )


@pytest.mark.asyncio
async def test_stderr_is_devnull_not_pipe(stdio_config: MCPServerConfig) -> None:
    """create_subprocess_exec must be called with stderr=DEVNULL, never PIPE.

    If stderr=PIPE were used and the subprocess wrote >64 KB of diagnostics,
    the OS pipe buffer would fill and the write(2) would block, deadlocking all
    subsequent tool calls.
    """
    captured_kwargs: dict = {}

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        proc = MagicMock()
        proc.returncode = None
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout = MagicMock()
        # Simulate server writing initialize response then initialized ack
        proc.stdout.readline = AsyncMock(
            side_effect=[
                b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{}}}\n',
                b"",  # EOF sentinel for any subsequent read
            ]
        )
        proc.terminate = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()
        return proc

    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ):
        client = MCPStdioClient(stdio_config)
        await client.start()
        await client.stop()

    assert "stderr" in captured_kwargs, "stderr kwarg was not passed to create_subprocess_exec"
    assert captured_kwargs["stderr"] == asyncio.subprocess.DEVNULL, (
        f"Expected stderr=DEVNULL ({asyncio.subprocess.DEVNULL!r}) to prevent pipe-buffer "
        f"deadlock, got {captured_kwargs['stderr']!r}. "
        "Using stderr=PIPE without a drain task deadlocks when the server writes >64 KB."
    )
    assert captured_kwargs["stderr"] != asyncio.subprocess.PIPE, (
        "stderr=PIPE is set — this will deadlock on >64 KB stderr output from the MCP server."
    )
