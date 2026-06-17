"""Supply-chain hardening for the MCP catalog and stdio transport.

Covers three confirmed risks (red-team finding):
  1. Every curated _KNOWN_SERVERS command pins an explicit npm @<version> so a
     `npx -y` launch can't hot-install the latest (possibly hijacked) package.
  2. transport.start() fails closed on an unpinned npm-family command — it
     raises MCPTransportError BEFORE create_subprocess_exec is reached.
  3. Remote-registry (_query_registry) entries can never carry a launchable
     command; any command field from network JSON is dropped (command == []).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.catalog import MCPCatalog, MCPCatalogEntry
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import (
    MCPStdioClient,
    MCPTransportError,
    _is_version_pinned_spec,
)

_NPM_LAUNCHERS = {"npx", "npm", "pnpm", "yarn"}


# ---------------------------------------------------------------------------
# 1. Every _KNOWN_SERVERS command is version-pinned.
# ---------------------------------------------------------------------------
class TestKnownServersArePinned:
    def test_every_known_server_npm_spec_is_version_pinned(self):
        catalog = MCPCatalog()
        servers = catalog.get_known_servers()
        assert servers, "expected curated known servers"
        for entry in servers:
            cmd = entry.command
            assert cmd, f"{entry.server_name} has no command"
            launcher = cmd[0].rsplit("/", 1)[-1].lower()
            if launcher not in _NPM_LAUNCHERS:
                continue
            # Find the package spec (first non-flag token after the launcher).
            spec = next((a for a in cmd[1:] if not a.startswith("-")), None)
            assert spec is not None, f"{entry.server_name}: no package spec"
            assert "@" in spec.lstrip("@"), (
                f"{entry.server_name}: spec {spec!r} has no @version pin"
            )
            assert _is_version_pinned_spec(spec), (
                f"{entry.server_name}: spec {spec!r} is not version-pinned — "
                "an unpinned npx -y spec hot-installs the latest from npm"
            )

    def test_no_known_server_uses_bare_unpinned_modelcontextprotocol_spec(self):
        catalog = MCPCatalog()
        for entry in catalog.get_known_servers():
            for arg in entry.command[1:]:
                if arg.startswith("@modelcontextprotocol/") and not arg.startswith("-"):
                    # Must carry a second '@' (the version) after the scope '@'.
                    assert "@" in arg[1:], (
                        f"{entry.server_name}: {arg!r} is unpinned"
                    )


class TestVersionPinPredicate:
    @pytest.mark.parametrize(
        "spec",
        [
            "@modelcontextprotocol/server-filesystem@2026.1.26",
            "@scope/pkg@0.6.2",
            "left-pad@1.3.0",
            "pkg@2",
        ],
    )
    def test_pinned_specs_pass(self, spec):
        assert _is_version_pinned_spec(spec) is True

    @pytest.mark.parametrize(
        "spec",
        [
            "@modelcontextprotocol/server-filesystem",
            "@scope/pkg",
            "left-pad",
            "@modelcontextprotocol/server-fetch@latest",
            "pkg@^1.0.0",
            "pkg@~1.0.0",
        ],
    )
    def test_unpinned_or_range_specs_fail(self, spec):
        assert _is_version_pinned_spec(spec) is False


# ---------------------------------------------------------------------------
# 2. transport.start() fails closed on an unpinned npm-family command.
# ---------------------------------------------------------------------------
def _make_config(**overrides: object) -> MCPServerConfig:
    defaults: dict = {
        "server_id": "sc-server",
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        "args": [],
        "env": {},
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


def _init_response() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}


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
    proc.wait = AsyncMock(return_value=0)
    return proc


class TestTransportRefusesUnpinned:
    async def test_start_refuses_unpinned_npx_without_spawning(self):
        # Unpinned spec — start() must raise BEFORE create_subprocess_exec.
        config = _make_config(
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem"]
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError, match="not version-pinned"):
                await client.start()
        mock_exec.assert_not_called()

    @pytest.mark.parametrize("launcher", sorted(_NPM_LAUNCHERS))
    async def test_start_refuses_every_npm_family_launcher(self, launcher):
        config = _make_config(command=[launcher, "@scope/evil-pkg"])
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            client = MCPStdioClient(config)
            with pytest.raises(MCPTransportError):
                await client.start()
        mock_exec.assert_not_called()

    async def test_start_allows_pinned_npx(self):
        config = _make_config(
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem@2026.1.26"]
        )
        proc = _mock_process([_init_response()])
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_start_allows_non_npm_command(self):
        # A plain interpreter command is not an npm launcher → unaffected.
        config = _make_config(command=["python", "-m", "some_mcp_server"])
        proc = _mock_process([_init_response()])
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()

    async def test_pinned_known_filesystem_server_passes_gate(self):
        # End-to-end: the real curated filesystem command survives the gate.
        fs = MCPCatalog().get_server("filesystem")
        assert fs is not None
        config = _make_config(command=list(fs.command))
        proc = _mock_process([_init_response()])
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            client = MCPStdioClient(config)
            await client.start()
        mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# 3. A hostile registry response yields entries with command == [].
# ---------------------------------------------------------------------------
class TestRegistryEntriesNeverLaunchable:
    @patch("urllib.request.urlopen")
    def test_hostile_smithery_response_drops_command(self, mock_urlopen):
        # A malicious registry tries to smuggle an unpinned launch command.
        hostile = {
            "servers": [
                {
                    "qualifiedName": "evil-server",
                    "displayName": "Evil",
                    "description": "pwn",
                    "useCount": 9999,
                    "command": ["npx", "-y", "@attacker/backdoor"],
                }
            ]
        }
        mock_urlopen.return_value = type("Resp", (), {
            "read": lambda self: json.dumps(hostile).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        catalog = MCPCatalog(registries=["smithery.ai"])
        results = catalog.search(query="evil", limit=10)
        assert len(results) == 1
        assert results[0].server_name == "evil-server"
        assert results[0].command == []

    @patch("urllib.request.urlopen")
    def test_hostile_mcp_registry_response_drops_command(self, mock_urlopen):
        hostile = {
            "servers": [
                {
                    "name": "evil-server",
                    "description": "pwn",
                    "command": ["npx", "-y", "@attacker/backdoor"],
                }
            ]
        }
        mock_urlopen.return_value = type("Resp", (), {
            "read": lambda self: json.dumps(hostile).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        catalog = MCPCatalog(registries=["registry.modelcontextprotocol.io"])
        results = catalog.search(query="evil", limit=10)
        assert len(results) == 1
        assert results[0].command == []

    def test_harden_helper_strips_command(self):
        entry = MCPCatalogEntry(
            server_name="x",
            source="smithery.ai",
            command=["npx", "-y", "@attacker/pkg"],
        )
        hardened = MCPCatalog._harden_registry_entry(entry)
        assert hardened.command == []
