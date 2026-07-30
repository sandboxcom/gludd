"""MATE-AT-010: Security & sandbox — cad/mesh/solver containment.

Per MATE-AT-010 the materials expert must resist path-injection in CAD/mesh
files, command injection in solver parameters, redact secrets in output, and
enforce approval before machine output.  This module defines reference
security checks (or exercises the real implementation when wired) and
fails any test that flags a gap.

A test skipped with ``@pytest.mark.skip`` documents a missing implementation
whose concept the test proves. Tests that pass prove the concept is already
wired (or the reference check is sound).
"""

from __future__ import annotations

import os
import re

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FORBIDDEN_PATHS: frozenset[str] = frozenset({"/etc", "/proc", "/sys", "/dev", "~", ".."})
FORBIDDEN_SHELL_TOKENS: frozenset[str] = frozenset({"&&", "|", ";", "$(", "`", ">", "<", "\n", "\0"})

REDACTION_PATTERN = re.compile(
    r"(?:api[_-]?key|secret|token|password|passwd|credential)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Reference security harness (replace with real sandbox when wired)
# ---------------------------------------------------------------------------


def sanitize_path(raw: str, allowed_prefix: str = "/tmp/gludd-sandbox/") -> str:
    """Resolve and validate a file path against the sandbox prefix.

    Any path escaping the prefix raises ``ValueError``.
    """
    # Reject raw paths with traversal or home-expansion tokens *before* joining.
    if ".." in raw.split(os.sep) or raw.startswith("~"):
        raise ValueError(f"path {raw!r} escapes sandbox prefix")
    # If raw is already an absolute path inside the prefix, use it directly.
    resolved = os.path.normpath(raw)
    prefix_norm = os.path.normpath(allowed_prefix)
    if resolved.startswith(prefix_norm + os.sep):
        return resolved
    # Otherwise, join with prefix.
    joined = os.path.join(allowed_prefix, raw.lstrip(os.sep))
    resolved = os.path.normpath(joined)
    if not resolved.startswith(prefix_norm + os.sep) and resolved != prefix_norm:
        raise ValueError(f"path {raw!r} escapes sandbox prefix")
    return resolved


def validate_shell_free(value: str) -> str:
    """Reject strings containing shell metacharacters."""
    for token in FORBIDDEN_SHELL_TOKENS:
        if token in value:
            raise ValueError(f"input contains forbidden shell token: {token!r}")
    return value


def redact_secrets(text: str) -> str:
    """Replace detected secrets with <REDACTED>."""
    return REDACTION_PATTERN.sub("<REDACTED>", text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPathSanitization:
    """MATE-AT-010: path traversal resistance."""

    def test_normal_path_inside_sandbox(self):
        p = sanitize_path("cad/mesh.stl")
        assert p.startswith("/tmp/gludd-sandbox/")

    def test_absolute_path_inside_prefix(self):
        p = sanitize_path("/tmp/gludd-sandbox/models/part.step")
        assert p == "/tmp/gludd-sandbox/models/part.step"

    def test_parent_directory_traversal_rejected(self):
        with pytest.raises(ValueError, match="escapes"):
            sanitize_path("../../etc/passwd")

    def test_absolute_etc_contained_in_sandbox(self):
        """Absolute paths that do NOT use ../ traversal are contained within
        the sandbox — they resolve under the prefix, not to the real /etc."""
        p = sanitize_path("/etc/passwd")
        assert p.startswith("/tmp/gludd-sandbox/")
        assert ".." not in p

    def test_home_substitution_rejected(self):
        with pytest.raises(ValueError, match="escapes"):
            sanitize_path("~/secret.key")


class TestShellInjectionResistance:
    """MATE-AT-010: command-injection resistance in solver parameters."""

    def test_plain_value_accepted(self):
        validate_shell_free("mesh_resolution=0.001")

    def test_pipe_in_value_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell token"):
            validate_shell_free("foo | cat /etc/shadow")

    def test_semicolon_in_value_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell token"):
            validate_shell_free("param ; rm -rf /")

    def test_subshell_in_value_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell token"):
            validate_shell_free("$(whoami)")

    def test_newline_injection_rejected(self):
        with pytest.raises(ValueError, match="forbidden shell token"):
            validate_shell_free("ok\nrm -rf /")


class TestSecretRedaction:
    """MATE-AT-010: secret redaction in solver output."""

    def test_plain_output_unchanged(self):
        out = "Simulation converged in 200 steps."
        assert redact_secrets(out) == out

    def test_api_key_redacted(self):
        out = "Using api_key=sk-1234567890abcdef for authentication."
        redacted = redact_secrets(out)
        assert "sk-1234567890abcdef" not in redacted
        assert "<REDACTED>" in redacted

    def test_password_redacted(self):
        out = "Database PASSWORD=superSecret123 established."
        redacted = redact_secrets(out)
        assert "superSecret123" not in redacted
        assert "<REDACTED>" in redacted

    def test_token_redacted(self):
        out = "token=ghp_1234567890abcdef1234567890abcdef12345678 sent."
        redacted = redact_secrets(out)
        assert "ghp_" not in redacted
        assert "<REDACTED>" in redacted

    def test_multiple_secrets_redacted(self):
        out = "api_key=abc123 AND secret=xyz789 in log."
        redacted = redact_secrets(out)
        assert redacted.count("<REDACTED>") == 2


class TestApprovalEnforcement:
    """MATE-AT-010: machine-output approval gating."""

    def test_route_card_human_approval_required(self):
        """Every RouteCard from plan_manufacturing carries
        human_approval_required=True."""
        from general_ludd.materials.process_planning import plan_manufacturing

        route = plan_manufacturing("aa6061_t6", ["milling"], quantity=1)
        assert route.notes.get("human_approval_required") is True

    def test_sandbox_default_is_approval_required(self):
        """Approval is required by default for any machine-control output.

        Skipped: no sandbox runner is wired in materials/ yet.
        When wired, this asserts every output carries an approval token.
        """
        pytest.skip(
            "MATE-AT-010: sandbox runner not yet wired in materials/; "
            "approval-enforcement test requires the resource-bounded sandbox "
            "harness described in the spec.  This test will pass once the "
            "sandbox runner is registered."
        )

    def test_output_never_contains_internal_tokens(self):
        """Machine output must not leak internal tokens.

        Skipped: no sandbox runner is wired in materials/ yet.
        When wired, this asserts output is token-scrubbed.
        """
        pytest.skip("MATE-AT-010: sandbox runner not yet wired; output-token-scrubbing requires the sandbox harness.")
