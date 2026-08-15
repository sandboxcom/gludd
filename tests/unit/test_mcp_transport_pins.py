"""Unit tests for MCP transport env-allowlist + version-pin negotiation (C4).

These exercise the transport's policy primitives DIRECTLY (no live MCP server,
no subprocess):

  * ``MCPStdioClient._build_env`` — the env scrub/allowlist (Finding 2): only
    ``_ENV_ALLOWLIST`` host vars survive, every other host var (secrets/creds)
    is stripped, and the server's own declared ``env`` is layered on top.
  * ``_is_version_pinned_spec`` — the concrete-version "pin" predicate that
    rejects dist-tags / ranges / bare names for npm-family launchers.
  * ``_validate_launch_command`` / ``_validate_package_spec`` — the pin-gate and
    package-spec injection guard, called directly (existing hardening tests only
    reach these via ``start()`` with mocked subprocesses).

Existing coverage that we do NOT duplicate: ``test_mcp_transport.py`` already
asserts the env scrub end-to-end via a mocked ``start()``
(``test_start_passes_minimal_env_not_full_host_env``) and the hardening file
covers allowlist/empty-argv/exec-resolution. Here we test the units in isolation
and add the pin-predicate edge cases those don't cover.
"""

from __future__ import annotations

import pytest

from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.transport import (
    _ENV_ALLOWLIST,
    MCPStdioClient,
    MCPTransportError,
    _is_version_pinned_spec,
    _validate_launch_command,
    _validate_package_spec,
)


def _make_config(**overrides: object) -> MCPServerConfig:
    defaults: dict = {
        "server_id": "pin-server",
        "command": ["npx", "-y", "@scope/server@1.0.0"],
        "args": [],
        "env": {},
    }
    defaults.update(overrides)
    return MCPServerConfig(**defaults)


# ---------------------------------------------------------------------------
# _build_env: env scrub / allowlist (Finding 2), tested directly
# ---------------------------------------------------------------------------
class TestBuildEnvAllowlist:
    def test_allowlisted_host_vars_kept(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("HOME", "/home/agent")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        client = MCPStdioClient(_make_config(env={}))
        env = client._build_env()
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/home/agent"
        assert env["LANG"] == "en_US.UTF-8"

    def test_non_allowlisted_host_secrets_stripped(self, monkeypatch):
        # The classic leak: host secrets/creds must NEVER reach the subprocess.
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
        monkeypatch.setenv("GLUDD_AUTH_PSK", "psk-secret")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
        client = MCPStdioClient(_make_config(env={}))
        env = client._build_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "GLUDD_AUTH_PSK" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_unset_allowlisted_var_omitted(self, monkeypatch):
        # A var on the allowlist that isn't set in the host env must not appear
        # as an empty/None entry.
        for key in _ENV_ALLOWLIST:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PATH", "/usr/bin")
        client = MCPStdioClient(_make_config(env={}))
        env = client._build_env()
        assert env == {"PATH": "/usr/bin"}
        assert "TMPDIR" not in env
        assert "LC_ALL" not in env

    def test_declared_server_env_layered_on_base(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        client = MCPStdioClient(_make_config(env={"FOO": "bar", "TOKEN": "t"}))
        env = client._build_env()
        assert env["PATH"] == "/usr/bin"
        assert env["FOO"] == "bar"
        assert env["TOKEN"] == "t"

    def test_declared_env_overrides_allowlisted_host_var(self, monkeypatch):
        # The server's declared env is applied via base.update(...) AFTER the
        # allowlist, so a server may legitimately override e.g. its own HOME.
        monkeypatch.setenv("HOME", "/home/host")
        client = MCPStdioClient(_make_config(env={"HOME": "/srv/home"}))
        env = client._build_env()
        assert env["HOME"] == "/srv/home"

    def test_allowlist_membership_is_the_expected_set(self):
        # Lock the allowlist down so a future widening (re-introducing a leak)
        # is caught by this test.
        assert set(_ENV_ALLOWLIST) == {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}


# ---------------------------------------------------------------------------
# _is_version_pinned_spec: the concrete-pin predicate
# ---------------------------------------------------------------------------
class TestVersionPinnedPredicate:
    @pytest.mark.parametrize(
        "spec",
        [
            "pkg@1.2.3",
            "pkg@2",
            "@scope/pkg@2026.1.26",
            "pkg@1.2.3-rc.1",
            "pkg@1.2.3+build.5",
        ],
    )
    def test_concrete_pins_accepted(self, spec):
        assert _is_version_pinned_spec(spec) is True

    @pytest.mark.parametrize(
        "spec",
        [
            "",
            "pkg",                 # bare name, no version
            "@scope/pkg",          # scoped, no version
            "pkg@latest",          # mutable dist-tag
            "pkg@next",            # mutable dist-tag
            "pkg@^1.0.0",          # caret range
            "pkg@~1.0.0",          # tilde range
            "pkg@>=1.0",           # comparator range
            "pkg@*",               # wildcard
        ],
    )
    def test_floating_or_bare_rejected(self, spec):
        assert _is_version_pinned_spec(spec) is False


# ---------------------------------------------------------------------------
# _validate_package_spec: pin gate + injection guard (called directly)
# ---------------------------------------------------------------------------
class TestValidatePackageSpec:
    def test_npm_unpinned_positional_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["npx", "-y", "some-pkg"], "npx")

    def test_npm_pinned_positional_ok(self):
        # No raise == accepted.
        _validate_package_spec(["npx", "-y", "some-pkg@1.2.3"], "npx")

    def test_shell_metacharacter_spec_rejected(self):
        with pytest.raises(MCPTransportError, match="shell metacharacters"):
            _validate_package_spec(["npx", "pkg; rm -rf /"], "npx")

    def test_package_flag_unpinned_rejected(self):
        # --package <spec> bypasses the positional check; it must still be pinned.
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["npx", "--package", "evil-pkg", "run"], "npx")

    def test_package_flag_pinned_then_binary_ok(self):
        # `npx --package pkg@1.2.3 some-cmd` — the trailing bare token is the
        # binary to run from the pinned package, NOT a second spec to validate.
        _validate_package_spec(
            ["npx", "--package", "pkg@1.2.3", "some-cmd"], "npx"
        )

    def test_inline_package_flag_unpinned_rejected(self):
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["npx", "--package=evil-pkg", "run"], "npx")

    def test_only_flags_no_spec_rejected(self):
        with pytest.raises(MCPTransportError, match="no package spec"):
            _validate_package_spec(["npx", "--yes"], "npx")

    def test_package_flag_missing_value_rejected(self):
        with pytest.raises(MCPTransportError, match="requires a following"):
            _validate_package_spec(["npx", "--package"], "npx")

    def test_uvx_unpinned_rejected_metachar_still_blocked(self):
        # H.10: uvx is a remote-fetch launcher, so bare specs are rejected for
        # supply-chain safety while metacharacter injection is still refused first.
        with pytest.raises(MCPTransportError, match=r"not.*version-pinned"):
            _validate_package_spec(["uvx", "mcp-server-git"], "uvx")
        with pytest.raises(MCPTransportError, match="shell metacharacters"):
            _validate_package_spec(["uvx", "pkg && evil"], "uvx")


# ---------------------------------------------------------------------------
# _validate_launch_command: the top-level gate wiring it all together
# ---------------------------------------------------------------------------
class TestValidateLaunchCommand:
    def test_empty_argv_rejected(self):
        with pytest.raises(MCPTransportError, match="empty"):
            _validate_launch_command([])

    def test_non_allowlisted_executable_rejected(self, monkeypatch):
        monkeypatch.delenv("GLUDD_MCP_ALLOW_ANY_EXEC", raising=False)
        with pytest.raises(MCPTransportError, match="allowlist"):
            _validate_launch_command(["/bin/sh", "-c", "echo hi"])

    def test_npx_unpinned_package_rejected_at_launch(self, monkeypatch):
        # Even with which() resolving, the pin gate fires for an npm-family spec.
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _c: "/usr/bin/npx")
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_launch_command(["npx", "-y", "unpinned-pkg"])

    def test_npx_pinned_package_passes_launch(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _c: "/usr/bin/npx")
        # No raise == accepted launch command.
        _validate_launch_command(["npx", "-y", "@scope/srv@1.0.0", "/tmp"])

    def test_bunx_in_npm_family_pin_gate_fires(self, monkeypatch):
        # D8 regression: bunx must be treated as an npm-family launcher so the
        # pin gate applies to it too.
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _c: "/usr/bin/bunx")
        with pytest.raises(MCPTransportError, match="version-pinned"):
            _validate_launch_command(["bunx", "some-pkg"])
        # And a pinned bunx spec passes.
        _validate_launch_command(["bunx", "some-pkg@1.0.0"])
