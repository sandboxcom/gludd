"""Coverage tests for src/general_ludd/mcp/ — security validators, registry collision, secrets fail-closed."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.secrets import resolve_mcp_env, scrub_mcp_config
from general_ludd.mcp.transport import (
    MCPStdioClient,
    MCPTransportError,
    _is_version_pinned_spec,
    _validate_launch_command,
    _validate_package_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stdio_config(**kwargs) -> MCPServerConfig:
    """Build a minimal stdio MCPServerConfig."""
    defaults = {"server_id": "test-server", "command": ["npx"], "args": []}
    defaults.update(kwargs)
    return MCPServerConfig(**defaults)


# ---------------------------------------------------------------------------
# _validate_launch_command
# ---------------------------------------------------------------------------


def test_validate_launch_command_empty_raises():
    with pytest.raises(MCPTransportError, match="empty"):
        _validate_launch_command([])


def test_validate_launch_command_non_allowlist_raises():
    with pytest.raises(MCPTransportError, match="allowlist"):
        _validate_launch_command(["curl", "http://example.com"])


def test_validate_launch_command_allow_any_exec_env_bypass(monkeypatch):
    monkeypatch.setenv("GLUDD_MCP_ALLOW_ANY_EXEC", "1")
    # "curl" is not in the allowlist, but env bypass is set.
    # It will pass the allowlist check, then hit the which/isfile check.
    # Mock which to return a path so it doesn't fail on resolution.
    with patch("general_ludd.mcp.transport.shutil.which", return_value="/usr/bin/curl"):
        # curl is not a remote-fetch launcher so no package-spec check follows
        _validate_launch_command(["curl", "http://example.com"])  # should not raise


def test_validate_launch_command_which_none_not_isfile_raises():
    with (
        patch("general_ludd.mcp.transport.shutil.which", return_value=None),
        patch("general_ludd.mcp.transport.os.path.isfile", return_value=False),
    ):
        with pytest.raises(MCPTransportError, match="could not be resolved"):
            _validate_launch_command(["python", "script.py"])


def test_validate_launch_command_abs_path_isfile_passes():
    # Absolute path that exists on disk — which=None is fine if isfile is True
    with (
        patch("general_ludd.mcp.transport.shutil.which", return_value=None),
        patch("general_ludd.mcp.transport.os.path.isfile", return_value=True),
    ):
        # python is on allowlist; abs path exists; no remote-fetch so no pkg spec needed
        _validate_launch_command(["/usr/bin/python", "script.py"])  # should not raise


# ---------------------------------------------------------------------------
# _validate_package_spec (via npx, which is npm-family)
# ---------------------------------------------------------------------------


def test_validate_package_spec_shell_metachar_raises():
    with pytest.raises(MCPTransportError, match="metachar"):
        _validate_package_spec(["npx", "pkg;rm -rf /"], "npx")


def test_validate_package_spec_unpinned_bare_name_raises():
    with pytest.raises(MCPTransportError, match="not.*version-pinned"):
        _validate_package_spec(["npx", "some-package"], "npx")


def test_validate_package_spec_flags_only_raises():
    with pytest.raises(MCPTransportError, match="no package spec"):
        _validate_package_spec(["npx", "--yes", "--quiet"], "npx")


def test_validate_package_spec_valid_pinned_passes():
    # Should not raise — pkg@1.2.3 is a valid pin
    _validate_package_spec(["npx", "pkg@1.2.3"], "npx")


def test_validate_package_spec_flags_then_pinned_passes():
    # Flags before the spec are fine
    _validate_package_spec(["npx", "-y", "@scope/pkg@2.0.0"], "npx")


# ---------------------------------------------------------------------------
# _is_version_pinned_spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("", False),
        ("pkg", False),
        ("@scope/pkg", False),
        ("pkg@latest", False),
        ("pkg@^1.0", False),
        ("pkg@~1.0.0", False),
        ("pkg@>=1.0", False),
        ("pkg@next", False),
        ("pkg@1.2.3", True),
        ("@scope/pkg@2.0", True),
        ("pkg@2", True),
        ("pkg@2026.1.26", True),
        ("pkg@1.0.0-beta.1", True),
    ],
)
def test_is_version_pinned_spec(spec: str, expected: bool):
    assert _is_version_pinned_spec(spec) is expected


# ---------------------------------------------------------------------------
# MCPStdioClient.start() rejects BEFORE subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_validates_before_subprocess():
    config = MCPServerConfig(
        server_id="bad-server",
        command=["curl"],  # not in allowlist
        args=["http://example.com"],
    )
    client = MCPStdioClient(config)

    mock_create = AsyncMock()
    with (
        patch("general_ludd.mcp.transport.asyncio.create_subprocess_exec", mock_create),
        patch("general_ludd.mcp.transport.shutil.which", return_value=None),
        patch("general_ludd.mcp.transport.os.path.isfile", return_value=False),
    ):
        with pytest.raises(MCPTransportError):
            await client.start()

    # Subprocess must never have been spawned
    mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# MCPToolRegistry — collision + ambiguity
# ---------------------------------------------------------------------------


def test_registry_collision_different_server_raises():
    reg = MCPToolRegistry()
    tool_a = MCPTool(name="my_tool", description="server A")
    tool_b = MCPTool(name="my_tool", description="server B")
    reg.register_tool("server-a", tool_a)
    with pytest.raises(ValueError, match="collision"):
        reg.register_tool("server-b", tool_b)


def test_registry_same_server_reregister_ok():
    reg = MCPToolRegistry()
    tool = MCPTool(name="my_tool")
    reg.register_tool("server-a", tool)
    # Re-registering the same server should not raise
    reg.register_tool("server-a", MCPTool(name="my_tool", description="updated"))


def test_registry_get_tool_unambiguous():
    reg = MCPToolRegistry()
    tool = MCPTool(name="search")
    reg.register_tool("server-a", tool)
    result = reg.get_tool("search")
    assert result is not None
    assert result.name == "search"


def test_registry_get_tool_ambiguous_returns_none():
    reg = MCPToolRegistry()
    reg.register_tool("server-a", MCPTool(name="search"))
    reg.register_tool("server-b", MCPTool(name="search"))
    result = reg.get_tool("search")
    assert result is None


def test_registry_get_tool_explicit_server_id_resolves_ambiguity():
    reg = MCPToolRegistry()
    reg.register_tool("server-a", MCPTool(name="search"))
    reg.register_tool("server-b", MCPTool(name="search"))
    result = reg.get_tool("search", server_id="server-b")
    assert result is not None
    assert result.server_id == "server-b"


# ---------------------------------------------------------------------------
# secrets.resolve_mcp_env
# ---------------------------------------------------------------------------


def test_resolve_mcp_env_alias_merged():
    config = MCPServerConfig(
        server_id="s",
        command=["python"],
        env={"STATIC": "static_val"},
        env_aliases={"MY_TOKEN": "vault/my-token"},
    )
    secrets_mgr = MagicMock()
    secrets_mgr.resolve.return_value = "resolved_secret"

    result = resolve_mcp_env(config, secrets_mgr)

    assert result["MY_TOKEN"] == "resolved_secret"
    assert result["STATIC"] == "static_val"


def test_resolve_mcp_env_required_alias_none_raises():
    config = MCPServerConfig(
        server_id="s",
        command=["python"],
        env_aliases={"API_KEY": "vault/api-key"},
    )
    secrets_mgr = MagicMock()
    secrets_mgr.resolve.return_value = None  # not found

    with pytest.raises(MCPTransportError, match="Required"):
        resolve_mcp_env(config, secrets_mgr)


def test_resolve_mcp_env_optional_alias_none_skipped():
    config = MCPServerConfig(
        server_id="s",
        command=["python"],
        env_aliases={"OPT_KEY": "vault/opt-key"},
        optional_env_aliases={"OPT_KEY"},
    )
    secrets_mgr = MagicMock()
    secrets_mgr.resolve.return_value = None  # not found, but optional

    result = resolve_mcp_env(config, secrets_mgr)
    # Should not raise; OPT_KEY is simply absent from result
    assert "OPT_KEY" not in result


def test_resolve_mcp_env_static_env_present_no_aliases():
    config = MCPServerConfig(
        server_id="s",
        command=["python"],
        env={"PLAIN_VAR": "hello"},
    )
    secrets_mgr = MagicMock()

    result = resolve_mcp_env(config, secrets_mgr)
    assert result["PLAIN_VAR"] == "hello"
    secrets_mgr.resolve.assert_not_called()


# ---------------------------------------------------------------------------
# secrets.scrub_mcp_config
# ---------------------------------------------------------------------------


def test_scrub_mcp_config_missing_file_returns_empty(tmp_path: Path):
    result = scrub_mcp_config(tmp_path / "nonexistent.yaml")
    assert result == []


def test_scrub_mcp_config_removes_secret_line(tmp_path: Path):
    cfg = tmp_path / "mcp.yaml"
    cfg.write_text(
        "servers:\n"
        "  my-server:\n"
        "    command: [python]\n"
        "    env:\n"
        "      API_KEY: super_secret_value\n"
        "      NORMAL_VAR: safe_value\n"
    )

    scrubbed = scrub_mcp_config(cfg)

    assert len(scrubbed) == 1
    assert "API_KEY" in scrubbed[0]
    remaining = cfg.read_text()
    assert "super_secret_value" not in remaining
    assert "NORMAL_VAR" in remaining
