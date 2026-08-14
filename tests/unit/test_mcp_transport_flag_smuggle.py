"""Regression tests for remote-fetch launcher flag smuggling.

The command is already an argv list, so shell parsing is intentionally out of
scope.  These tests cover option values that alter package resolution *before*
the pinned positional package is reached.
"""

from __future__ import annotations

import pytest

from general_ludd.mcp.transport import MCPTransportError, _validate_package_spec


@pytest.mark.parametrize(
    ("launcher", "command"),
    [
        ("npx", ["npx", "--prefix=/tmp/attacker", "server@1.2.3"]),
        ("npx", ["npx", "--cwd", "/tmp/attacker", "server@1.2.3"]),
        ("pnpm", ["pnpm", "-C/tmp/attacker", "server@1.2.3"]),
        ("uvx", ["uvx", "--directory", "/tmp/attacker", "server==1.2.3"]),
        ("uvx", ["uvx", "--project=/tmp/attacker", "server==1.2.3"]),
        ("uvx", ["uvx", "--config-file=/tmp/uv.toml", "server==1.2.3"]),
    ],
)
def test_package_resolution_directory_redirect_is_rejected(
    launcher: str,
    command: list[str],
) -> None:
    with pytest.raises(MCPTransportError, match="directory-redirect"):
        _validate_package_spec(command, launcher)


@pytest.mark.parametrize(
    "flag",
    [
        "--other-package=evil@latest",
        "--registry=https://registry.invalid;payload",
    ],
)
def test_unknown_inline_flag_cannot_smuggle_package_or_metacharacters(flag: str) -> None:
    with pytest.raises(MCPTransportError, match="flag value"):
        _validate_package_spec(["npx", flag, "server@1.2.3"], "npx")


@pytest.mark.parametrize(
    ("launcher", "command"),
    [
        ("npx", ["npx", "--yes", "server@1.2.3"]),
        ("npx", ["npx", "--package=server@1.2.3", "server"]),
        ("uvx", ["uvx", "--no-cache", "server==1.2.3"]),
    ],
)
def test_benign_and_validated_package_flags_remain_accepted(
    launcher: str,
    command: list[str],
) -> None:
    _validate_package_spec(command, launcher)
