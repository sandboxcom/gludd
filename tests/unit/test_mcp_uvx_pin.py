"""Unit tests for H.10: uvx package-spec version-pin enforcement.

Before H.10, uvx specs were exempt from version-pin checking (only npm-family
launchers were gated). After H.10, uvx specs MUST be version-pinned via
==X.Y.Z or @X.Y.Z — bare names and range specifiers are rejected.
"""

from __future__ import annotations

import pytest

from general_ludd.mcp.transport import (
    MCPTransportError,
    _is_uvx_version_pinned_spec,
    _validate_package_spec,
)


# ---------------------------------------------------------------------------
# _is_uvx_version_pinned_spec predicate
# ---------------------------------------------------------------------------
class TestUvxVersionPinnedPredicate:
    @pytest.mark.parametrize(
        "spec",
        [
            "pkg==1.2.3",
            "pkg==2",
            "pkg==1.2.3rc1",
            "pkg==1.2.3+build.5",
            "pkg==2024.1.1",
            "pkg[extra]==1.2.3",
            "pkg[extra1,extra2]==1.2.3",
            "pkg@1.2.3",
            "pkg@2",
            "pkg@1.2.3-rc.1",
            "pkg@1.2.3+build.5",
        ],
    )
    def test_concrete_pins_accepted(self, spec):
        assert _is_uvx_version_pinned_spec(spec) is True

    @pytest.mark.parametrize(
        "spec",
        [
            "",
            "pkg",
            "pkg>=1.0",
            "pkg<=2.0",
            "pkg>1.0",
            "pkg<2.0",
            "pkg~=1.2.3",
            "pkg!=1.2.3",
            "pkg>=1.0,<2.0",
            "pkg>=1.0.0,!=1.0.1",
            "pkg==1.*",
            "pkg==1.2.*",
        ],
    )
    def test_floating_or_bare_rejected(self, spec):
        assert _is_uvx_version_pinned_spec(spec) is False


# ---------------------------------------------------------------------------
# _validate_package_spec: uvx pin gate integration
# ---------------------------------------------------------------------------
class TestUvxValidatePackageSpec:
    def test_uvx_unpinned_positional_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["uvx", "mcp-server-git"], "uvx")

    def test_uvx_pinned_double_equals_accepted(self):
        _validate_package_spec(["uvx", "mcp-server-git==1.2.3"], "uvx")

    def test_uvx_pinned_at_accepted(self):
        _validate_package_spec(["uvx", "mcp-server-git@1.0.0"], "uvx")

    def test_uvx_range_rejected(self):
        # > in >= hits shell metacharacter check first, which is correct
        # defense-in-depth; still rejected.
        with pytest.raises(MCPTransportError, match="shell metacharacters"):
            _validate_package_spec(["uvx", "mcp-server-git>=1.0"], "uvx")

    def test_uvx_compatible_release_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["uvx", "mcp-server-git~=1.2"], "uvx")

    def test_uvx_version_glob_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["uvx", "mcp-server-git==1.*"], "uvx")

    def test_uvx_metacharacter_still_blocked(self):
        with pytest.raises(MCPTransportError, match="shell metacharacters"):
            _validate_package_spec(["uvx", "pkg && evil"], "uvx")

    def test_uvx_with_extras_pinned_accepted(self):
        _validate_package_spec(
            ["uvx", "mcp-server-git[extra]==1.2.3"], "uvx"
        )

    def test_uvx_only_flags_no_spec_rejected(self):
        with pytest.raises(MCPTransportError, match="no package spec"):
            _validate_package_spec(["uvx", "--from"], "uvx")

    def test_npm_still_works(self):
        _validate_package_spec(["npx", "some-pkg@1.2.3"], "npx")
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_package_spec(["npx", "unpinned-pkg"], "npx")
