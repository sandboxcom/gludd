"""Hardening tests for MCP stdio subprocess launch (#65).

The launcher must:
  * never shell-interpret the command (argv list only — no shell=True);
  * reject an empty argv;
  * reject an executable whose basename is not on the allowlist unless the
    operator explicitly opts in via GLUDD_MCP_ALLOW_ANY_EXEC=1;
  * reject an executable that does not resolve on PATH (and is not an existing
    absolute path);
  * reject injection-y package specs handed to npx/uvx (leading-dash flag
    injection, shell metacharacters).
Legitimate npx/uvx/python/node MCP servers must keep working.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


def _make_config(**overrides: object) -> MCPServerConfig:
    defaults: dict = {
        "server_id": "harden-server",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "args": ["/tmp"],
        "env": {},
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _ok_proc() -> MagicMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(
        return_value=b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
    )
    proc.stderr = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.kill = MagicMock()
    return proc


class TestNoShellInterpretation:
    async def test_uses_exec_not_shell(self):
        # The hardened launcher must call create_subprocess_exec, never the
        # shell variant.
        config = _make_config()
        proc = _ok_proc()

        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec, patch(
            "asyncio.create_subprocess_shell"
        ) as mock_shell:
            client = MCPStdioClient(config)
            await client.start()

        mock_exec.assert_called_once()
        mock_shell.assert_not_called()
        # argv is passed as separate positional args (a list), never a string.
        pos_args, _ = mock_exec.call_args
        assert list(pos_args) == ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]


class TestEmptyArgv:
    async def test_empty_command_rejected(self):
        config = _make_config(command=[], args=[])
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="empty"):
                await client.start()
        mock_exec.assert_not_called()


class TestExecutableAllowlist:
    async def test_disallowed_executable_rejected(self):
        # /bin/sh is not a known MCP runtime — must be rejected by default.
        config = _make_config(command=["/bin/sh"], args=["-c", "echo hi"])
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="not in the MCP executable allowlist"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_allow_any_exec_opt_in(self, monkeypatch):
        # Operators can opt out of the allowlist explicitly.
        monkeypatch.setenv("GLUDD_MCP_ALLOW_ANY_EXEC", "1")
        config = _make_config(command=["my-custom-mcp"], args=[])
        proc = _ok_proc()
        with patch("shutil.which", return_value="/usr/local/bin/my-custom-mcp"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_allowed_runtimes_pass(self):
        # Each runtime invoked in its canonical, legitimate form.
        invocations = {
            "npx": ["npx", "-y", "@scope/server"],
            "uvx": ["uvx", "mcp-server-git"],
            "python": ["python", "-m", "srv"],
            "python3": ["python3", "-m", "srv"],
            "node": ["node", "server.js"],
        }
        for exe, command in invocations.items():
            config = _make_config(command=command, args=[])
            proc = _ok_proc()
            with patch("shutil.which", return_value=f"/usr/bin/{exe}"), patch(
                "asyncio.create_subprocess_exec", return_value=proc
            ) as mock_exec:
                client = MCPStdioClient(config)
                await client.start()
            mock_exec.assert_called_once()


class TestExecutableResolves:
    async def test_unresolvable_executable_rejected(self):
        config = _make_config(command=["npx", "-y", "pkg"], args=[])
        with patch("shutil.which", return_value=None), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="could not be resolved"):
                await client.start()
        mock_exec.assert_not_called()


class TestPackageSpecInjection:
    async def test_leading_dash_package_rejected(self):
        # `npx --some-flag` injection: the package arg must not start with '-'.
        config = _make_config(command=["npx"], args=["--evil-flag"])
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="package spec"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_shell_metacharacter_package_rejected(self):
        config = _make_config(command=["uvx"], args=["pkg; rm -rf /"])
        with patch("shutil.which", return_value="/usr/bin/uvx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="package spec"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_npx_dash_y_then_package_ok(self):
        # `npx -y <pkg>` is the canonical legitimate form and must pass.
        config = _make_config(
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            args=["/tmp"],
        )
        proc = _ok_proc()
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_uvx_package_with_version_pin_ok(self):
        config = _make_config(command=["uvx"], args=["mcp-server-git==0.1.0"])
        proc = _ok_proc()
        with patch("shutil.which", return_value="/usr/bin/uvx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()
