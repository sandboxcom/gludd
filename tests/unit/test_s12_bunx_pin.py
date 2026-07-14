"""S.12: bunx must be in _NPM_FAMILY_LAUNCHERS exactly once — no overwrite.

D8/CA-M1: A second definition of _NPM_FAMILY_LAUNCHERS would overwrite the
first, potentially omitting bunx and skipping the version-pin gate. This test
proves the fix: a single definition at module top includes bunx.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from general_ludd.mcp.transport import (
    _NPM_FAMILY_LAUNCHERS,
    _REMOTE_FETCH_LAUNCHERS,
    MCPTransportError,
    _validate_launch_command,
    _validate_package_spec,
)


class TestS12SingleDefinition:
    """Prove _NPM_FAMILY_LAUNCHERS is defined exactly once in transport.py."""

    def test_bunx_is_in_npm_family(self):
        assert "bunx" in _NPM_FAMILY_LAUNCHERS

    def test_bunx_is_in_remote_fetch(self):
        assert "bunx" in _REMOTE_FETCH_LAUNCHERS

    def test_npm_family_has_exactly_one_definition_in_source(self):
        transport_path = (
            Path(__file__).parent.parent.parent / "src" / "general_ludd" / "mcp" / "transport.py"
        )
        source = transport_path.read_text()
        tree = ast.parse(source)
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "_NPM_FAMILY_LAUNCHERS"
                for target in node.targets
            )
        ]
        assert len(assignments) == 1, (
            f"Expected exactly 1 assignment to _NPM_FAMILY_LAUNCHERS, "
            f"found {len(assignments)}"
        )


class TestS12BunxPinGate:
    """Prove the pin gate fires for bunx in all code paths."""

    def test_bunx_unpinned_package_spec_rejected(self):
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["bunx", "some-pkg"], "bunx")

    def test_bunx_unpinned_scoped_package_rejected(self):
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["bunx", "@scope/pkg"], "bunx")

    def test_bunx_pinned_package_spec_accepted(self):
        _validate_package_spec(["bunx", "some-pkg@1.0.0"], "bunx")

    def test_bunx_pinned_scoped_package_accepted(self):
        _validate_package_spec(["bunx", "@scope/pkg@2.3.4"], "bunx")

    def test_bunx_major_only_pin_accepted(self):
        _validate_package_spec(["bunx", "pkg@3"], "bunx")

    def test_bunx_latest_tag_rejected(self):
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["bunx", "pkg@latest"], "bunx")

    def test_bunx_range_rejected(self):
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["bunx", "pkg@^1.0.0"], "bunx")

    def test_bunx_package_flag_unpinned_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["bunx", "--package", "evil-pkg", "run"], "bunx")

    def test_bunx_inline_package_flag_unpinned_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["bunx", "--package=evil-pkg", "run"], "bunx")

    def test_bunx_metachar_spec_rejected(self):
        with pytest.raises(MCPTransportError, match="shell metacharacters"):
            _validate_package_spec(["bunx", "pkg; rm -rf /"], "bunx")

    def test_bunx_only_flags_no_spec_rejected(self):
        with pytest.raises(MCPTransportError, match="no package spec"):
            _validate_package_spec(["bunx", "--yes"], "bunx")


class TestS12BunxLaunchGate:
    """Prove bunx unpinned fails the full launch-command gate."""

    def test_bunx_unpinned_launch_rejected(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _c: "/usr/bin/bunx")
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_launch_command(["bunx", "some-pkg"])

    def test_bunx_pinned_launch_accepted(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _c: "/usr/bin/bunx")
        _validate_launch_command(["bunx", "some-pkg@1.0.0"])
