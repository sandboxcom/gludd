"""Tests for flag-smuggling guards in MCP transport package-spec validation.

The validator must reject flag-style args that embed a package spec or path-escape
(e.g. ``--package=evil@latest``, ``-C /tmp``, ``--prefix=/evil``), while keeping
legitimate benign flags (``-y``, ``--yes``, ``--no-audit``) and version-pinned
package specs working.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError


def _make_config(**overrides: object) -> MCPServerConfig:
    defaults: dict = {
        "server_id": "smuggle-test",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem@2026.1.26"],
        "args": ["/tmp"],
        "env": {},
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


class TestFlagSmuggleRejection:
    """Flag args that embed a package spec or path-redirect must be rejected."""

    async def test_package_flag_with_at_version_rejected(self):
        """``--package=evil@latest`` embeds a package-version spec → reject."""
        config = _make_config(
            command=["npx", "--package=evil@latest", "cmd"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="embeds a package spec"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_package_flag_with_shell_meta_rejected(self):
        """``--package=evil;cmd`` embeds shell metachars in a flag value → reject."""
        config = _make_config(
            command=["npx", "--package=evil;cmd", "some-pkg@1.0.0"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="embeds a package spec"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_dash_C_flag_rejected_for_npx(self):
        """`-C /tmp` is a directory-redirect flag → reject."""
        config = _make_config(
            command=["npx", "-C", "/tmp", "some-pkg@1.0.0"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="directory-redirect"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_prefix_flag_rejected(self):
        """``--prefix=/evil`` is a directory-redirect flag → reject."""
        config = _make_config(
            command=["npm", "--prefix=/evil", "run", "start"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npm"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="directory-redirect"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_cwd_flag_rejected(self):
        """``--cwd /evil`` is a directory-redirect flag → reject."""
        config = _make_config(
            command=["npx", "--cwd", "/evil", "some-pkg@1.0.0"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="directory-redirect"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_dash_C_rejected_for_uvx(self):
        """`-C` is blocked for uvx too."""
        config = _make_config(
            command=["uvx", "-C", "/evil", "mcp-server-git"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/uvx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="directory-redirect"):
                await client.start()
        mock_exec.assert_not_called()


class TestBenignFlagsPass:
    """Benign flags that don't smuggle specs must continue to work."""

    async def test_dash_y_flag_passes(self):
        """`npx -y pkg@1.0.0` is the canonical form and must pass."""
        config = _make_config(
            command=["npx", "-y", "@scope/server@1.0.0"],
            args=[],
        )
        from unittest.mock import AsyncMock, MagicMock

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

        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_double_dash_yes_flag_passes(self):
        """`npx --yes pkg@1.0.0` must pass — ``--yes`` has no ``=`` value."""
        from unittest.mock import AsyncMock, MagicMock

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

        config = _make_config(
            command=["npx", "--yes", "@scope/server@2.0.0"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_no_audit_flag_passes(self):
        """`npx --no-audit pkg@1.0.0` must pass — ``--no-audit`` is a bool flag."""
        from unittest.mock import AsyncMock, MagicMock

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

        config = _make_config(
            command=["npx", "--no-audit", "@scope/server@3.0.1"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()


class TestExistingPinnedSpecStillValid:
    """The existing pinned-spec validation path must still work correctly."""

    async def test_pinned_scoped_package_accepted(self):
        """``@modelcontextprotocol/server-filesystem@2026.1.26`` is a valid pin."""
        from unittest.mock import AsyncMock, MagicMock

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

        config = _make_config(
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem@2026.1.26"],
            args=["/tmp"],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec", return_value=proc
        ) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_unpinned_package_still_rejected(self):
        """Existing guard: bare package name (no version) must still be rejected."""
        config = _make_config(
            command=["npx", "some-package"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="not version-pinned"):
                await client.start()
        mock_exec.assert_not_called()

    async def test_latest_tag_still_rejected(self):
        """Existing guard: ``pkg@latest`` must still be rejected as unpinned."""
        config = _make_config(
            command=["npx", "evil@latest"],
            args=[],
        )
        with patch("shutil.which", return_value="/usr/bin/npx"), patch(
            "asyncio.create_subprocess_exec"
        ) as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="not version-pinned"):
                await client.start()
        mock_exec.assert_not_called()
