"""E2E tests: security and sandbox subsystem workflows.

Covers the full lifecycle of six security domains:
  1. Auth providers — OAuth2 flow, API key auth, PSK verification, admin tokens
  2. RBAC — role resolution, permission checking, role hierarchy, spec intersection
  3. Sandbox execution — process isolation, resource limits, filesystem restrictions
  4. Input sanitization — path traversal prevention, SSRF guards, credential redaction
  5. Secret management — OpenBao integration, secret versioning, access policies
  6. Audit trail — event logging, STS audit log, tamper detection

Uses mocked hvac for Vault, temp directories for path isolation, in-memory
STS registry, and ProcessExecutor with real resource limits where applicable.
No external services required.

Run:  make test-specific TESTFILE=tests/e2e/test_security_sandbox_workflows.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ——— Shared test tokens (seed data only) ——————————————————————————————
_PSK = "e2e-security-sandbox-psk-token-abcdef123"  # pragma: allowlist secret
_API_KEY = "sk-proj-e2e-sandbox-test-key-abcdef1234567890"  # pragma: allowlist secret
_ADMIN_TOKEN = "admin-e2e-token-value-abcdef123456"  # pragma: allowlist secret

# ========================================================================
# 1. AUTH PROVIDERS  —  OAuth2, PSK, admin tokens, bearer extraction
# ========================================================================


class TestAuthProviders:
    """PSK verification, admin tokens, bearer parsing, auth posture loading."""

    # —— PSK verification ——————————————————————————————————————————————

    def test_verify_psk_matches_hmac_compare_digest(self):
        from general_ludd.security.auth import verify_psk

        assert verify_psk("secret-token", "secret-token") is True
        assert verify_psk("secret-token", "different-token") is False

    def test_verify_psk_rejects_empty_presented(self):
        from general_ludd.security.auth import verify_psk

        assert verify_psk("", "secret") is False
        assert verify_psk("token", "") is False
        assert verify_psk("", "") is False

    def test_verify_psk_rejects_non_string_inputs(self):
        import pytest as _pytest

        from general_ludd.security.auth import verify_psk

        with _pytest.raises(TypeError):
            verify_psk(None, "secret")  # type: ignore[arg-type]
        with _pytest.raises(TypeError):
            verify_psk("token", None)  # type: ignore[arg-type]
        assert verify_psk("valid", "valid") is True

    # —— Bearer token parsing ————————————————————————————————————————————

    def test_check_bearer_token_extracts_after_bearer_prefix(self):
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Bearer abc123", "abc123") is True
        assert check_bearer_token("Bearer abc123", "xyz789") is False

    def test_check_bearer_token_rejects_missing_bearer_prefix(self):
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Basic abc123", "abc123") is False
        assert check_bearer_token("", "abc123") is False
        assert check_bearer_token("abc123", "abc123") is False

    def test_check_bearer_token_rejects_extra_whitespace(self):
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Bearer   abc123  ", "abc123") is False

    def test_bearer_prefix_is_case_sensitive(self):
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("bearer abc", "abc") is False
        assert check_bearer_token("BEARER abc", "abc") is False

    # —— Admin token —————————————————————————————————————————————————————

    def test_check_admin_token_matches_hmac(self):
        from general_ludd.security.auth import check_admin_token

        assert check_admin_token("token-abc", "token-abc") is True
        assert check_admin_token("token-abc", "token-xyz") is False

    def test_check_admin_token_rejects_empty(self):
        from general_ludd.security.auth import check_admin_token

        assert check_admin_token("", "secret") is False
        assert check_admin_token("token", "") is False

    # —— Auth posture —————————————————————————————————————

    def test_load_auth_posture_with_psk_configured(self):
        from general_ludd.security.auth import load_auth_posture

        env = {"GLUDD_AUTH_PSK": "my-psk", "GLUDD_REQUIRE_AUTH": "1"}
        posture = load_auth_posture("daemon", env=env)
        assert posture.psk == "my-psk"
        assert posture.require_auth is True
        assert posture.no_auth is False
        assert posture.surface == "daemon"

    def test_load_auth_posture_no_psk_fail_closed_default(self):
        from general_ludd.security.auth import load_auth_posture

        env: dict[str, str] = {}
        posture = load_auth_posture("worker", env=env)
        assert posture.psk == ""
        assert posture.no_auth is True
        assert posture.require_auth is True  # fail-closed default

    def test_load_auth_posture_explicitly_disabled(self):
        from general_ludd.security.auth import load_auth_posture

        env = {"GLUDD_PSK_DISABLE": "1"}
        posture = load_auth_posture("daemon", env=env)
        assert posture.require_auth is False

    def test_load_auth_posture_allow_no_auth(self):
        from general_ludd.security.auth import load_auth_posture

        env = {"GLUDD_ALLOW_NO_AUTH": "on"}
        posture = load_auth_posture("worker", env=env)
        assert posture.require_auth is False

    def test_require_auth_env_detects_all_truthy_values(self):
        from general_ludd.security.auth import require_auth_env

        for val in ("1", "true", "yes", "on", "True", "YES"):
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is True

        for val in ("0", "false", "no", "off", ""):
            assert require_auth_env({"GLUDD_REQUIRE_AUTH": val}) is False


# ========================================================================
# 2. RBAC  —  role resolution, permission checking, hierarchy, intersection
# ========================================================================


class TestRBAC:
    """PermissionSpec parsing, validation, intersection, subsets, defaults."""

    # —— Default specs —————————————————————————————————————————————————————

    def test_default_spec_build_has_read_on_build_path(self):
        from general_ludd.security.permissions import default_spec

        spec = default_spec("build")
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]
        assert "secret/data/gludd/build/*" in cap.constraints.get("openbao_paths", [])

    def test_default_spec_subagent_has_no_capabilities(self):
        from general_ludd.security.permissions import default_spec

        spec = default_spec("subagent")
        assert spec.capabilities == []
        assert spec.capability_for("secret:openbao") is None

    def test_default_spec_unknown_falls_back_to_subagent(self):
        from general_ludd.security.permissions import default_spec

        spec = default_spec("nonexistent-role")
        assert spec.capabilities == []

    # —— Human specs ——————————————————————————————————————————————————

    def test_default_human_admin_has_full_access(self):
        from general_ludd.security.permissions import PermissionSubject, default_human_spec

        spec = default_human_spec("human-admin")
        assert spec.subject == PermissionSubject.HUMAN
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert set(cap.actions) == {"read", "write", "list", "delete"}

    def test_default_human_viewer_is_restrictive(self):
        from general_ludd.security.permissions import default_human_spec

        spec = default_human_spec("human-viewer")
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]
        assert "secret/data/gludd/read-only/*" in cap.constraints.get("openbao_paths", [])

    def test_default_human_unknown_falls_back_to_viewer(self):
        from general_ludd.security.permissions import default_human_spec

        spec = default_human_spec("unknown-role")
        cap = spec.capability_for("secret:openbao")
        assert cap.actions == ["read"]

    # —— PermissionSpec.is_denied ——————————————————————————————————————

    def test_is_denied_false_when_no_deny_list(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"]),
            ],
        )
        assert spec.is_denied("secret:openbao", "read") is False

    def test_is_denied_true_when_action_in_deny_list(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"]),
            ],
            denied=[
                Capability(resource="secret:openbao", actions=["write"]),
            ],
        )
        assert spec.is_denied("secret:openbao", "write") is True
        assert spec.is_denied("secret:openbao", "read") is False

    def test_is_denied_empty_deny_actions_blocks_all(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(resource="secret:openbao", actions=[]),
            ],
        )
        assert spec.is_denied("secret:openbao", "read") is True
        assert spec.is_denied("secret:openbao", "write") is True

    def test_is_denied_with_path_constraint(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="test",
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["build/prod-signing-key"]},
                ),
            ],
        )
        assert spec.is_denied("secret:openbao", "read", "build/prod-signing-key") is True
        assert spec.is_denied("secret:openbao", "read", "build/config") is False

    # —— PermissionSpecParser ——————————————————————————————————————————

    def test_parser_parse_from_yaml_string(self):
        from general_ludd.security.permissions import PermissionSpecParser

        yaml_str = """\
version: 1
agent_type: test
max_sts_ttl_seconds: 600
capabilities:
  - resource: secret:openbao
    actions: ["read"]
    constraints:
      openbao_paths: ["test/*"]
denied: []
"""
        spec = PermissionSpecParser.parse(yaml_str)
        assert spec.agent_type == "test"
        assert spec.max_sts_ttl_seconds == 600
        cap = spec.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]

    def test_parser_parse_with_denied(self):
        from general_ludd.security.permissions import PermissionSpecParser

        yaml_str = """\
version: 1
agent_type: restricted
capabilities:
  - resource: secret:openbao
    actions: ["read", "write"]
    constraints:
      openbao_paths: ["build/*"]
denied:
  - resource: secret:openbao
    actions: ["write"]
    constraints:
      openbao_paths: ["build/prod-key"]
"""
        spec = PermissionSpecParser.parse(yaml_str)
        assert len(spec.denied) == 1
        assert spec.denied[0].actions == ["write"]

    def test_parser_validate_detects_missing_constraints(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        spec = PermissionSpec(
            agent_type="bad",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"]),
            ],
        )
        errors = PermissionSpecParser.validate(spec)
        assert len(errors) >= 1
        assert any("missing required constraint" in e.lower() for e in errors)

    def test_parser_validate_detects_overlapping_deny(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        spec = PermissionSpec(
            agent_type="bad",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["*"]},
                ),
            ],
        )
        errors = PermissionSpecParser.validate(spec)
        assert any("overlapping" in e.lower() for e in errors)

    # —— is_subset —————————————————————————————————————————————————————

    def test_is_subset_allows_narrower_spec(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        issuer = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "delete"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is True

    def test_is_subset_rejects_escalation(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        issuer = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    def test_is_subset_rejects_denied_action(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        issuer = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
            denied=[
                Capability(resource="secret:openbao", actions=["write"]),
            ],
        )
        requested = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["write"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        assert PermissionSpecParser.is_subset(requested, issuer) is False

    # —— intersection ———————————————————————————————————————————————

    def test_intersection_narrows_actions(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
            PermissionSubject,
        )

        a = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "delete"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        b = PermissionSpec(
            agent_type="human-operator",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert result.subject == PermissionSubject.STS_TOKEN
        cap = result.capability_for("secret:openbao")
        assert cap is not None
        assert cap.actions == ["read"]

    def test_intersection_keeps_overlapping_paths_only(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        a = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["build/*", "shared/*"]},
                ),
            ],
        )
        b = PermissionSpec(
            agent_type="human-operator",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["shared/*", "public/*"]},
                ),
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        cap = result.capability_for("secret:openbao")
        assert cap is not None
        assert cap.constraints.get("openbao_paths") == ["shared/*"]

    def test_intersection_unions_denied_lists(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionSpec,
            PermissionSpecParser,
        )

        a = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["*"]},
                ),
            ],
            denied=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["secret-a"]})
            ],
        )
        b = PermissionSpec(
            agent_type="human-operator",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["*"]},
                ),
            ],
            denied=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["secret-b"]})
            ],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert len(result.denied) == 2

    def test_intersection_clamps_ttl_to_min(self):
        from general_ludd.security.permissions import (
            PermissionSpec,
            PermissionSpecParser,
        )

        a = PermissionSpec(
            agent_type="admin",
            max_sts_ttl_seconds=7200,
            capabilities=[],
        )
        b = PermissionSpec(
            agent_type="human-operator",
            max_sts_ttl_seconds=600,
            capabilities=[],
        )
        result = PermissionSpecParser.intersection(a, b)
        assert result.max_sts_ttl_seconds == 600


# ========================================================================
# 3. SANDBOX EXECUTION  —  process isolation, resource limits, confinement
# ========================================================================


class TestSandboxExecution:
    """Process isolation, resource limits, path confinement, cleanup, network."""

    # —— Process limits ————————————————————————————————————————————————

    def test_process_limits_default_values(self):
        from general_ludd.sandbox.process_executor import ProcessLimits

        limits = ProcessLimits()
        assert limits.memory_mb is None
        assert limits.cpu_seconds is None

    def test_process_limits_with_all_fields(self):
        from general_ludd.sandbox.process_executor import ProcessLimits

        limits = ProcessLimits(
            memory_mb=256, cpu_seconds=60, max_file_size=10_000_000, max_open_files=64, max_processes=20
        )
        assert limits.memory_mb == 256
        assert limits.cpu_seconds == 60
        assert limits.max_file_size == 10_000_000
        assert limits.max_open_files == 64
        assert limits.max_processes == 20

    # —— ProcessExecutor ————————————————————————————————————————————————

    def test_process_executor_runs_simple_command(self):
        from general_ludd.sandbox.process_executor import ProcessExecutor

        executor = ProcessExecutor(timeout=10)
        result = executor.execute("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.was_killed is False

    def test_process_executor_kills_on_timeout(self):
        from general_ludd.sandbox.process_executor import ProcessExecutor

        executor = ProcessExecutor(timeout=1)
        result = executor.execute("sleep 10")
        assert result.was_killed is True

    def test_process_executor_returns_stderr(self):
        from general_ludd.sandbox.process_executor import ProcessExecutor

        executor = ProcessExecutor(timeout=10)
        result = executor.execute("python -c 'import sys; sys.stderr.write(\"err-msg\")'")
        assert "err-msg" in result.stderr

    # —— SandboxExecutor (sandbox_exec) —————————————————————————————————

    def test_sandbox_executor_runs_command(self):
        from general_ludd.sandbox_exec.executor import SandboxExecutor

        executor = SandboxExecutor(timeout=10)
        result = executor.execute("echo sandbox-test")
        assert result.returncode == 0
        assert "sandbox-test" in result.stdout

    def test_sandbox_executor_enforces_command_length_limit(self):
        from general_ludd.sandbox_exec.executor import SandboxExecutor

        executor = SandboxExecutor(timeout=10, max_output_bytes=10_000)
        long_cmd = "echo " + "x" * (executor.max_command_chars + 1)
        with pytest.raises(OSError, match="exceeds sandbox limit"):
            executor.execute(long_cmd)

    # —— SandboxEnforcer ————————————————————————————————————————————————

    def test_sandbox_enforcer_verify_ready_auto_creates_jail(self):
        from general_ludd.sandbox.enforcer import SandboxConfig, SandboxEnforcer

        config = SandboxConfig()  # no jail_dir -> auto-mkdtemp
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        assert enforcer.is_ready is True
        assert enforcer.jail_dir
        assert Path(enforcer.jail_dir).is_dir()

    def test_sandbox_enforcer_verify_ready_raises_on_missing_dir(self):
        from general_ludd.sandbox.enforcer import (
            SandboxConfig,
            SandboxEnforcer,
            SandboxNotAvailableError,
        )

        config = SandboxConfig(jail_dir="/nonexistent/path/sandbox-jail")
        enforcer = SandboxEnforcer(config)
        with pytest.raises(SandboxNotAvailableError):
            enforcer.verify_ready()

    def test_sandbox_enforcer_fail_open_warns_but_proceeds(self):
        from general_ludd.sandbox.enforcer import SandboxConfig, SandboxEnforcer

        config = SandboxConfig(fail_open=True)
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        result = enforcer.execute("echo fail-open-test")
        assert result.returncode == 0

    def test_sandbox_enforcer_execute_fail_closed_raises_without_verify(self):
        from general_ludd.sandbox.enforcer import (
            SandboxConfig,
            SandboxEnforcer,
            SandboxNotAvailableError,
        )

        config = SandboxConfig(fail_open=False)
        enforcer = SandboxEnforcer(config)
        with pytest.raises(SandboxNotAvailableError, match="not verified"):
            enforcer.execute("echo test")

    def test_sandbox_enforcer_confine_path_rejects_escape(self):
        from general_ludd.sandbox.enforcer import (
            PathEscapeError,
            SandboxConfig,
            SandboxEnforcer,
        )

        config = SandboxConfig()
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path("../../../etc/passwd")

    def test_sandbox_enforcer_confine_path_allows_subpath(self):
        from general_ludd.sandbox.enforcer import SandboxConfig, SandboxEnforcer

        with tempfile.TemporaryDirectory() as td:
            subpath = Path(td, "work")
            subpath.mkdir()
            enforcer = SandboxEnforcer(SandboxConfig(jail_dir=td))
            enforcer.verify_ready()
            result = enforcer.confine_path(str(subpath))
            assert Path(result) == subpath.resolve()

    def test_sandbox_enforcer_execute_confines_workdir(self):
        from general_ludd.sandbox.enforcer import SandboxConfig, SandboxEnforcer

        with tempfile.TemporaryDirectory() as td:
            config = SandboxConfig(jail_dir=td)
            enforcer = SandboxEnforcer(config)
            enforcer.verify_ready()
            result = enforcer.execute("echo test", workdir=td)
            assert result.returncode == 0

    # —— Resource limits ————————————————————————————————

    def test_resource_limits_default_light(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits.default_light()
        assert limits.cpu_shares == 1024
        assert limits.memory_bytes == 256 * 1024 * 1024
        assert limits.timeout_seconds == 120

    def test_resource_limits_default_medium(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits.default_medium()
        assert limits.cpu_shares == 2048
        assert limits.memory_bytes == 512 * 1024 * 1024

    def test_resource_limits_default_heavy(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits.default_heavy()
        assert limits.cpu_shares == 4096
        assert limits.memory_bytes == 1024 * 1024 * 1024

    def test_resource_limits_exceed_memory(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits(memory_bytes=100_000_000)
        assert limits.exceed_memory(200_000_000) is True
        assert limits.exceed_memory(50_000_000) is False

    def test_resource_limits_exceed_timeout(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits(timeout_seconds=60)
        assert limits.exceed_timeout(120.0) is True
        assert limits.exceed_timeout(30.0) is False

    def test_resource_limits_to_docker_args(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits(memory_bytes=256 * 1024 * 1024, cpu_shares=1024)
        args = limits.to_docker_args()
        assert "--memory" in args
        assert "--cpu-shares" in args

    def test_resource_limits_to_process_limits(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits(memory_bytes=256 * 1024 * 1024, cpu_shares=2048)
        pl = limits.to_process_limits()
        assert pl["memory_mb"] == 256
        assert pl["cpu_seconds"] == 2

    # —— Network policy ———————————————————————————————

    def test_network_policy_fully_isolated(self):
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy.fully_isolated()
        assert policy.allow_outbound is False
        assert policy.allow_inbound is False
        assert policy.is_isolated() is True

    def test_network_policy_allows_specific_host(self):
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy(
            allow_outbound=True,
            allowed_hosts=["api.example.com"],
        )
        assert policy.allows_host("api.example.com") is True
        assert policy.allows_host("other.example.com") is False

    def test_network_policy_blocked_hosts_take_priority(self):
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy(
            allow_outbound=True,
            allowed_hosts=["*.example.com"],
            blocked_hosts=["evil.example.com"],
        )
        assert policy.allows_host("evil.example.com") is False

    def test_network_policy_port_restrictions(self):
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy(
            allow_outbound=True,
            allowed_ports=[443, 80],
            blocked_ports=[8080],
        )
        assert policy.allows_port(443) is True
        assert policy.allows_port(8080) is False
        assert policy.allows_port(9999) is False

    def test_network_policy_to_docker_args_isolation(self):
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy.fully_isolated()
        args = policy.to_docker_args()
        assert "--network" in args
        assert "none" in args

    # —— Security policy ————————————————————————————————

    def test_security_policy_minimal_is_restrictive(self):
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy.minimal()
        assert policy.read_only_root is True
        assert policy.privileged is False
        assert policy.no_new_privileges is True
        assert policy.is_restrictive() is True

    def test_security_policy_docker_default(self):
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy.default_docker()
        assert policy.read_only_root is True
        assert policy.privileged is False
        assert policy.is_restrictive() is True

    def test_security_policy_to_docker_args(self):
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy(read_only_root=True, apparmor_profile="gludd-profile")
        args = policy.to_docker_args()
        assert "--read-only" in args
        assert "apparmor=gludd-profile" in args

    def test_security_policy_no_new_privileges_default(self):
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy()
        args = policy.to_docker_args()
        assert any("no-new-privileges" in a for a in args)

    def test_security_policy_privileged_overrides(self):
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy(
            privileged=True, read_only_root=False, no_new_privileges=False, allow_privilege_escalation=True
        )
        assert policy.is_restrictive() is False

    # —— Cleanup ————————————————————————————————————————————

    def test_cleanup_manager_tracks_pending_resources(self):
        from general_ludd.sandbox.cleanup import CleanupManager

        mgr = CleanupManager()
        mgr.track("docker_container", "abc123")
        assert mgr.pending_count() == 1

    def test_cleanup_manager_history_grows(self):
        from general_ludd.sandbox.cleanup import CleanupManager

        mgr = CleanupManager()
        mgr.track("docker_container", "test-001")
        mgr.cleanup_all()
        assert mgr.pending_count() == 0

    def test_cleanup_manager_unknown_resource_type(self):
        from general_ludd.sandbox.cleanup import CleanupManager

        mgr = CleanupManager()
        mgr.track("unknown_type", "id-001")
        assert mgr.pending_count() == 1
        mgr.cleanup_all()
        assert mgr.pending_count() == 1

    # —— Resource limits on Kubernetes (dry) ——————————————————-

    def test_resource_limits_to_kubernetes(self):
        from general_ludd.sandbox.resource_limits import ResourceLimits

        limits = ResourceLimits(memory_bytes=512 * 1024 * 1024, cpu_shares=2048, disk_bytes=10 * 1024 * 1024)
        k8s = limits.to_kubernetes_resources()
        assert "limits" in k8s
        assert "requests" in k8s
        assert "memory" in k8s["limits"]
        assert "ephemeral-storage" in k8s["limits"]

    def test_network_policy_to_kubernetes(self):
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy(allow_outbound=True, allowed_hosts=["10.0.0.0/8"])
        k8s = policy.to_kubernetes_policy("default", {"app": "gludd"})
        assert k8s["kind"] == "NetworkPolicy"
        assert k8s["spec"]["egress"] is not None

    def test_security_policy_to_kubernetes(self):
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy(seccomp_profile="localhost/gludd-seccomp.json")
        sec = policy.to_kubernetes_context()
        assert sec["readOnlyRootFilesystem"] is True
        assert sec["privileged"] is False
        assert "seccompProfile" in sec


# ========================================================================
# 4. INPUT SANITIZATION  —  path traversal, SSRF, credential redaction
# ========================================================================


class TestInputSanitization:
    """Path traversal guards, SSRF blocking, credential redaction, URL validation."""

    # —— Path sanitization ———————————————————————————————————————

    def test_sanitize_path_rejects_traversal(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("../etc/passwd") is None
        assert sanitize_path("..\\windows\\system32") is None
        assert sanitize_path("foo/../../bar") is None

    def test_sanitize_path_rejects_bare_double_dot(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("..") is None
        assert sanitize_path("./foo/..") is None

    def test_sanitize_path_rejects_absolute(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("/etc/passwd") is None

    def test_sanitize_path_allows_normal_paths(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("foo/bar/baz.txt") == "foo/bar/baz.txt"
        assert sanitize_path("./config.json") == "config.json"
        assert sanitize_path("file-name.py") == "file-name.py"

    def test_sanitize_path_rejects_empty(self):
        from general_ludd.security.sanitize import sanitize_path

        assert sanitize_path("") is None
        assert sanitize_path("   ") is None

    # —— Skill name sanitization ——————————————————————————————————

    def test_sanitize_skill_name_rejects_path_separators(self):
        from general_ludd.security.sanitize import sanitize_skill_name

        assert sanitize_skill_name("foo/bar") is None
        assert sanitize_skill_name("foo\\bar") is None

    def test_sanitize_skill_name_rejects_dot_references(self):
        from general_ludd.security.sanitize import sanitize_skill_name

        assert sanitize_skill_name(".") is None
        assert sanitize_skill_name("..") is None
        assert sanitize_skill_name("hidden..traverse") is None

    def test_sanitize_skill_name_allows_normal_names(self):
        from general_ludd.security.sanitize import sanitize_skill_name

        assert sanitize_skill_name("my-skill") == "my-skill"
        assert sanitize_skill_name("build agent v2") == "build agent v2"

    def test_sanitize_skill_name_rejects_nul_byte(self):
        from general_ludd.security.sanitize import sanitize_skill_name

        assert sanitize_skill_name("safe\x00escape") is None

    # —— Job ID sanitization ———————————————————————————————————————

    def test_sanitize_job_id_allows_valid(self):
        from general_ludd.security.sanitize import sanitize_job_id

        assert sanitize_job_id("JOB_2026-07-25_A1") == "JOB_2026-07-25_A1"
        assert sanitize_job_id("ABC123") == "ABC123"

    def test_sanitize_job_id_rejects_slashes(self):
        from general_ludd.security.sanitize import sanitize_job_id

        assert sanitize_job_id("job/../escape") is None
        assert sanitize_job_id("job\\escape") is None

    def test_sanitize_job_id_rejects_unusual_chars(self):
        from general_ludd.security.sanitize import sanitize_job_id

        assert sanitize_job_id("job; rm -rf /") is None

    # —— Path confinement ——————————————————————————————————————————

    def test_confine_path_allows_child(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as base:
            (Path(base) / "child").mkdir()
            result = confine_path("child", base)
            assert result is not None
            assert "child" in result

    def test_confine_path_rejects_escape(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as base:
            assert confine_path("../etc/passwd", base) is None

    def test_confine_path_rejects_absolute(self):
        from general_ludd.security.sanitize import confine_path

        with tempfile.TemporaryDirectory() as base:
            assert confine_path("/etc/passwd", base) is None

    def test_confine_path_rejects_empty(self):
        from general_ludd.security.sanitize import confine_path

        assert confine_path("", "/tmp") is None
        assert confine_path("/tmp", "") is None

    def test_confine_path_rejects_nul_byte(self):
        from general_ludd.security.sanitize import confine_path

        assert confine_path("foo\x00bar", "/tmp") is None

    def test_confine_path_multi_allows_any_root(self):
        from general_ludd.security.sanitize import confine_path_multi

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            (Path(a) / "file-a").write_text("a")
            (Path(b) / "file-b").write_text("b")
            result_a = confine_path_multi("file-a", [a])
            result_b = confine_path_multi("file-b", [b])
            assert result_a is not None
            assert result_b is not None

    def test_is_path_within_true_for_subpath(self):
        from general_ludd.security.sanitize import is_path_within

        with tempfile.TemporaryDirectory() as base:
            assert is_path_within("child.txt", base) is True

    # —— Workspace roots ———————————————————————————————————————————

    def test_workspace_roots_includes_cwd_and_tmp(self):
        from general_ludd.security.sanitize import workspace_roots

        roots = workspace_roots()
        assert len(roots) >= 2
        assert any("tmp" in r for r in roots)

    def test_workspace_roots_extra_roots_appended(self):
        from general_ludd.security.sanitize import workspace_roots

        with tempfile.TemporaryDirectory() as extra:
            roots = workspace_roots(extra)
            assert any(extra in r for r in roots)

    # —— SSRF guards ———————————————————————————————————————————————

    def test_is_safe_fetch_url_rejects_http(self):
        from general_ludd.security.sanitize import is_safe_fetch_url

        assert is_safe_fetch_url("http://github.com") is False

    def test_is_safe_fetch_url_allows_https_public(self):
        from general_ludd.security.sanitize import is_safe_fetch_url

        assert is_safe_fetch_url("https://github.com/gludd/repo") is True

    def test_is_safe_fetch_url_rejects_loopback(self):
        from general_ludd.security.sanitize import is_safe_fetch_url

        assert is_safe_fetch_url("https://localhost:8200") is False
        assert is_safe_fetch_url("https://127.0.0.1/secret") is False

    def test_is_safe_fetch_url_rejects_private_ip(self):
        from general_ludd.security.sanitize import is_safe_fetch_url

        assert is_safe_fetch_url("https://10.0.0.1/admin") is False
        assert is_safe_fetch_url("https://192.168.1.100/api") is False

    def test_is_safe_fetch_url_rejects_metadata_ip(self):
        from general_ludd.security.sanitize import is_safe_fetch_url

        assert is_safe_fetch_url("https://169.254.169.254/latest/meta-data") is False

    def test_is_safe_fetch_url_rejects_empty(self):
        from general_ludd.security.sanitize import is_safe_fetch_url

        assert is_safe_fetch_url("") is False

    def test_validate_fetch_url_returns_url_when_safe(self):
        from general_ludd.security.sanitize import validate_fetch_url

        result = validate_fetch_url("https://api.example.com/data")
        assert result == "https://api.example.com/data"

    def test_validate_fetch_url_returns_none_when_unsafe(self):
        from general_ludd.security.sanitize import validate_fetch_url

        assert validate_fetch_url("http://api.example.com") is None
        assert validate_fetch_url("") is None

    # —— SSRF host_is_blocked ————————————————————————————————————

    def test_host_is_blocked_empty_host(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("") is True

    def test_host_is_blocked_localhost_names(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("localhost") is True
        assert host_is_blocked("foo.localhost") is True
        assert host_is_blocked("metadata.google.internal") is True

    def test_host_is_blocked_private_ips(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("10.0.0.1") is True
        assert host_is_blocked("192.168.1.1") is True
        assert host_is_blocked("172.16.0.1") is True

    def test_host_is_blocked_nul_byte_smuggling(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("example.com\x00.evil") is True

    def test_host_is_blocked_single_label_hostname(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("vault") is True
        assert host_is_blocked("grafana") is True

    def test_host_is_blocked_trailing_dot_bypass(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("127.0.0.1.") is True

    def test_host_is_blocked_nonstandard_ip_encoding(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("2130706433") is True

    def test_is_url_blocked_https_with_blocked_host(self):
        from general_ludd.security.ssrf import is_url_blocked

        assert is_url_blocked("https://127.0.0.1:8200/secret") is True

    def test_is_url_blocked_https_with_public_host(self):
        from general_ludd.security.ssrf import is_url_blocked

        assert is_url_blocked("https://api.github.com/repos", {"https"}) is False

    def test_host_is_blocked_ipv6_loopback(self):
        from general_ludd.security.ssrf import host_is_blocked

        assert host_is_blocked("::1") is True
        assert host_is_blocked("[::1]") is True

    # —— Credential redaction ————————————————————————————————————

    def test_sanitize_error_message_redacts_openai_key(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Error: sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 was rejected"
        sanitized = sanitize_error_message(msg)
        assert "sk-proj-" not in sanitized
        assert "REDACTED_OPENAI_KEY" in sanitized

    def test_sanitize_error_message_redacts_loopback_ip(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Connection refused: 127.0.0.1:8200"
        sanitized = sanitize_error_message(msg)
        assert "127.0.0.1" not in sanitized
        assert "REDACTED_LOOPBACK_IP" in sanitized

    def test_sanitize_error_message_redacts_bearer_token(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "Authorization: Bearer secret-token-value-123"
        sanitized = sanitize_error_message(msg)
        assert "secret-token-value-123" not in sanitized
        assert "REDACTED_BEARER_TOKEN" in sanitized

    def test_sanitize_error_message_redacts_private_ip(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "host 10.10.1.5 refused connection"
        sanitized = sanitize_error_message(msg)
        assert "10.10.1.5" not in sanitized
        assert "REDACTED_PRIVATE_IP" in sanitized

    def test_sanitize_error_message_redacts_metadata_ip(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "error from 169.254.169.254"
        sanitized = sanitize_error_message(msg)
        assert "169.254.169.254" not in sanitized
        assert "REDACTED_METADATA_IP" in sanitized

    def test_sanitize_error_message_passthrough_harmless(self):
        from general_ludd.security.sanitize import sanitize_error_message

        msg = "File not found: main.py"
        assert sanitize_error_message(msg) == msg

    def test_sanitize_error_message_handles_empty(self):
        from general_ludd.security.sanitize import sanitize_error_message

        assert sanitize_error_message("") == ""


# ========================================================================
# 5. SECRET MANAGEMENT  —  OpenBao, secret versioning, access policies
# ========================================================================


class TestSecretManagement:
    """OpenBao config, secret alias registration, STS-backed permission gating."""

    def _mock_hvac_client(self) -> MagicMock:
        client = MagicMock()
        client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "e2e-secret-value"}}}
        client.secrets.kv.v2.create_or_update_secret.return_value = {}
        client.secrets.kv.v2.delete_metadata_and_all_versions.return_value = {}
        client.secrets.kv.v2.list_metadata.return_value = {"data": {"keys": ["key1", "key2"]}}
        return client

    def test_openbao_config_defaults(self):
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig()
        assert config.mode == "auto"
        assert config.kv_mount == "secret"

    def test_openbao_config_external_requires_https(self):
        from general_ludd.secrets.config import OpenBaoConfig

        config = OpenBaoConfig(
            mode="external", external_url="https://bao.example.com:8200", external_token="s.test-token"
        )
        assert config.mode == "external"
        assert config.external_url == "https://bao.example.com:8200"

    def test_secret_alias_roundtrip(self):
        from general_ludd.secrets.manager import SecretAlias

        alias = SecretAlias("api_key", "projects/app/api_key")
        assert alias.alias == "api_key"
        assert alias.path == "projects/app/api_key"

    def test_secrets_manager_register_and_resolve_alias(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretAlias, SecretsManager

        mock_client = self._mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.register_alias(SecretAlias("db_password", "projects/db/password"))
        value = mgr.resolve("db_password")
        assert value == "e2e-secret-value"

    def test_secrets_manager_bootstrap_local(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager()
        result = mgr.bootstrap_local()
        assert result.initialized is True
        assert result.url == "http://localhost:8200"
        assert result.token.startswith("s.local-dev-")

    def test_secrets_manager_connect_external_https(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.ext-token-abc",
        )
        mgr = SecretsManager(config=config)
        mgr.connect()
        assert mgr._client is not None

    def test_secrets_manager_permission_gating_allows_read(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="reader",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["projects/*"]},
                ),
            ],
        )
        mock_client = self._mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)
        result = mgr.read_secret("projects/app/key")
        assert result is not None

    def test_secrets_manager_permission_gating_denies_write(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretPermissionDeniedError, SecretsManager
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="reader",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["projects/*"]},
                ),
            ],
        )
        mock_client = self._mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError, match="write"):
            mgr.write_secret("projects/app/key", {"val": "x"})

    def test_secrets_manager_list_secrets(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = self._mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        keys = mgr.list_secrets("test/prefix")
        assert keys == ["key1", "key2"]

    def test_secrets_manager_delete_secret(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = self._mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.delete_secret("test/to-delete")
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once()

    def test_secrets_manager_write_secret_rejects_traversal(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager()
        with pytest.raises(ValueError, match=r".."):
            mgr.write_secret("bad/../../../escape", {"val": "x"})

    def test_secrets_manager_write_secret_allows_valid_paths(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = self._mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        for p in ["projects/test/secret", "build/config/key"]:
            mgr.write_secret(p, {"val": "ok"})
        assert mock_client.secrets.kv.v2.create_or_update_secret.call_count == 2

    def test_secret_permission_denied_error_includes_context(self):
        from general_ludd.secrets.manager import SecretPermissionDeniedError

        exc = SecretPermissionDeniedError(
            path="projects/secret/key",
            action="write",
            agent_type="viewer",
            allowed_patterns=["shared/*"],
        )
        msg = str(exc)
        assert "projects/secret/key" in msg
        assert "viewer" in msg
        assert "shared/*" in msg


# ========================================================================
# 6. AUDIT TRAIL  —  STS audit log, event querying, log integrity
# ========================================================================


class TestAuditTrail:
    """STS audit log events, query filtering, and lifecycle tracking."""

    def test_audit_log_records_issue_use_expiry_chain(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsAuditLog, StsIssuer

        issuer = StsIssuer(clock=lambda: 1000.0)
        audit = StsAuditLog()
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[Capability(resource="secret:openbao", actions=["read", "write"])],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)

        audit.record_issue(token)
        audit.record_use(
            token.token_id, Capability(resource="secret:openbao", actions=["read"]), "projects/app/api_key"
        )
        audit.record_expiry(token.token_id)

        events = audit.query()
        assert len(events) == 3
        assert events[0]["event"] == "issued"
        assert events[1]["event"] == "used"
        assert events[2]["event"] == "expired"

    def test_audit_log_query_filter_by_agent_id(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsAuditLog, StsIssuer

        issuer = StsIssuer()
        audit = StsAuditLog()
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token_a = issuer.issue(issuer_spec, subject_spec, "admin-1", "agent-a", ttl_seconds=3600)
        token_b = issuer.issue(issuer_spec, subject_spec, "admin-2", "agent-b", ttl_seconds=3600)
        audit.record_issue(token_a)
        audit.record_issue(token_b)

        assert len(audit.query(agent_id="agent-a")) == 1
        assert len(audit.query(agent_id="admin-1")) == 1

    def test_audit_log_query_filter_by_capability(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsAuditLog, StsIssuer

        issuer = StsIssuer()
        audit = StsAuditLog()
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"]),
                Capability(resource="file:", actions=["read", "write"], constraints={"path_prefix": "/repo/"}),
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        audit.record_use(token.token_id, Capability(resource="secret:openbao", actions=["read"]), "path/a")
        audit.record_use(token.token_id, Capability(resource="agent:", actions=[]), "agent/status")

        assert len(audit.query(capability="secret:openbao")) == 1
        assert len(audit.query(capability="agent:")) == 1

    def test_audit_log_query_filter_by_time_window(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsAuditLog, StsIssuer

        now = time.time()
        issuer = StsIssuer(clock=lambda: now)
        audit = StsAuditLog()
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        audit.record_issue(token)

        assert len(audit.query(since=now - 1)) == 1
        assert len(audit.query(since=now + 999999)) == 0

    def test_audit_log_empty_when_no_events(self):
        from general_ludd.security.sts import StsAuditLog

        audit = StsAuditLog()
        assert audit.query() == []


# ========================================================================
# 7. FIX-NOT-DISABLE  —  policy detection for disabling intent
# ========================================================================


class TestFixNotDisable:
    """Detects disabling intent in action descriptions."""

    def test_disable_patterns_all_detected(self):
        from general_ludd.security.fix_not_disable import DISABLE_PATTERNS, is_disabling_action

        for pattern in DISABLE_PATTERNS:
            description = f"we should {pattern} this feature"
            assert is_disabling_action(description) is True, f"Pattern '{pattern}' not detected in '{description}'"

    def test_legitimate_actions_not_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        for action in [
            "implement the new feature",
            "refactor the event loop",
            "correct the type annotation",
            "add a new test",
            "update the documentation",
            "improve error handling",
        ]:
            assert is_disabling_action(action) is False, f"'{action}' should not be flagged"

    def test_policy_fail_closed_blocks_all_disabling(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, _ = policy.check_action("skip the failing test")
        assert allowed is False

    def test_policy_fail_open_allows_disable_with_repair(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, _ = policy.check_action("fix the disabled check by repairing it")
        assert allowed is True


# ========================================================================
# 8. STS TOKEN LIFECYCLE  —  issue, resolve, validate, expire, revoke
# ========================================================================


class TestSTSTokenLifecycle:
    """STS token issuance, validation, expiry, revocation, and use tracking."""

    def test_sts_issuer_validates_ttl_cap(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        issuer_spec = PermissionSpec(
            agent_type="admin",
            max_sts_ttl_seconds=300,
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]}),
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]}),
            ],
        )
        issuer = StsIssuer(clock=lambda: 0.0)
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=999999)
        assert token.expires_at == 300.0  # clamped to issuer's max

    def test_sts_issuer_propagates_denials(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"], constraints={"openbao_paths": ["*"]}),
            ],
            denied=[
                Capability(
                    resource="secret:openbao", actions=["write"], constraints={"openbao_paths": ["build/prod-key"]}
                ),
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"], constraints={"openbao_paths": ["*"]}),
            ],
        )
        issuer = StsIssuer(clock=lambda: 1000.0)
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        assert len(token.spec.denied) >= 1

    def test_sts_issuer_record_use_increments_count(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        issuer = StsIssuer(clock=lambda: 1000.0)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        assert token.use_count == 0

        issuer.record_use(token.token_id)
        issuer.record_use(token.token_id)
        retrieved = issuer.get_token(token.token_id)
        assert retrieved is not None
        assert retrieved.use_count == 2

    def test_sts_issuer_revoke_drops_token(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        issuer = StsIssuer(clock=lambda: 1000.0)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        assert issuer.revoke(token.token_id) is True
        assert issuer.revoke(token.token_id) is False

    def test_sts_issuer_expired_token_validate_false(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        clock_val = [1000.0]

        def clock() -> float:
            return clock_val[0]

        issuer = StsIssuer(clock=clock)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=1)
        assert issuer.validate(token, Capability(resource="secret:openbao", actions=["read"])) is True

        clock_val[0] = 2000.0
        assert issuer.validate(token, Capability(resource="secret:openbao", actions=["read"])) is False

    def test_sts_issuer_list_active_evicts_expired(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        clock_val = [1000.0]

        def clock() -> float:
            return clock_val[0]

        issuer = StsIssuer(clock=clock)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read"], constraints={"openbao_paths": ["*"]})
            ],
        )
        issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=1)
        assert len(issuer.list_active()) == 1

        clock_val[0] = 2000.0
        assert len(issuer.list_active()) == 0


# ========================================================================
# 9. SECCOMP  —  BPF filter construction and introspection (non-Linux)
# ========================================================================


class TestSeccompFilter:
    """Seccomp BPF filter construction, syscall lookup, and introspection."""

    def test_seccomp_filter_default_denies_escape_syscalls(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter.default()
        assert f.is_denied("mount") is True
        assert f.is_denied("unshare") is True
        assert f.is_denied("pivot_root") is True
        assert f.is_denied("setns") is True
        assert f.is_denied("chroot") is True

    def test_seccomp_filter_allows_normal_syscalls(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter.default()
        assert f.is_allowed("read") is True
        assert f.is_allowed("write") is True
        assert f.is_allowed("open") is True
        assert f.is_allowed("close") is True

    def test_seccomp_filter_denied_not_allowed(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter.default()
        assert f.is_allowed("mount") is False
        assert f.is_allowed("unshare") is False

    def test_seccomp_filter_builds_bpf_program_x86_64(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter.default()
        program = f.build_bpf(arch="x86_64")
        assert len(program) > 10
        assert all(len(insn) == 4 for insn in program)

    def test_seccomp_filter_builds_bpf_program_aarch64(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter.default()
        program = f.build_bpf(arch="aarch64")
        assert len(program) > 10

    def test_seccomp_filter_strict_mode_default_deny(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter(default_action="errno")
        assert f.is_allowed("read") is True
        assert f.is_allowed("unknown_syscall_xyz") is False

    def test_seccomp_filter_is_supported_returns_bool(self):
        from general_ludd.security.seccomp import SeccompFilter

        result = SeccompFilter.is_supported()
        assert isinstance(result, bool)

    def test_seccomp_filter_apply_is_nop_on_non_linux(self):
        from general_ludd.security.seccomp import SeccompFilter

        f = SeccompFilter.default()
        if not sys.platform.startswith("linux"):
            assert f.apply() is False


# ========================================================================
# 10. SSH KEY ROTATION  —  key generation, listing, scrubbing, history
# ========================================================================


class TestSSHKeyRotation:
    """SSH key pair lifecycle: generate, list, scrub, rotation history."""

    def test_generate_key_pair_creates_both_files(self):
        import shutil

        from general_ludd.security.ssh_key_rotation import generate_key_pair

        store = tempfile.mkdtemp()
        try:
            meta = generate_key_pair("test-key", keystore_dir=store)
            assert meta.name == "test-key"
            assert "stub" in meta.fingerprint
            assert (Path(store) / "test-key").is_file()
            assert (Path(store) / "test-key.pub").is_file()
        finally:
            shutil.rmtree(store, ignore_errors=True)

    def test_generate_key_pair_raises_on_duplicate(self):
        import shutil

        from general_ludd.security.ssh_key_rotation import generate_key_pair

        store = tempfile.mkdtemp()
        try:
            generate_key_pair("dup-key", keystore_dir=store)
            with pytest.raises(FileExistsError):
                generate_key_pair("dup-key", keystore_dir=store)
        finally:
            shutil.rmtree(store, ignore_errors=True)

    def test_list_keys_returns_generated_keys(self):
        import shutil

        from general_ludd.security.ssh_key_rotation import generate_key_pair, list_keys

        store = tempfile.mkdtemp()
        try:
            generate_key_pair("key-a", keystore_dir=store)
            generate_key_pair("key-b", keystore_dir=store)
            keys = list_keys(keystore_dir=store)
            assert len(keys) == 2
            names = {k.name for k in keys}
            assert "key-a" in names
            assert "key-b" in names
        finally:
            shutil.rmtree(store, ignore_errors=True)

    def test_list_keys_empty_for_missing_dir(self):
        from general_ludd.security.ssh_key_rotation import list_keys

        keys = list_keys(keystore_dir="/nonexistent/ssh/dir")
        assert keys == []

    def test_scrub_key_removes_both_files(self):
        import shutil

        from general_ludd.security.ssh_key_rotation import generate_key_pair, list_keys, scrub_key

        store = tempfile.mkdtemp()
        try:
            generate_key_pair("scrub-me", keystore_dir=store)
            assert len(list_keys(keystore_dir=store)) == 1
            assert scrub_key("scrub-me", keystore_dir=store) is True
            assert len(list_keys(keystore_dir=store)) == 0
        finally:
            shutil.rmtree(store, ignore_errors=True)

    def test_scrub_key_returns_false_for_missing(self):
        from general_ludd.security.ssh_key_rotation import scrub_key

        assert scrub_key("nonexistent-key", keystore_dir="/tmp/not-a-dir") is False

    def test_rotation_history_empty_when_no_history(self):
        from general_ludd.security.ssh_key_rotation import rotation_history

        events = rotation_history(keystore_dir="/nonexistent/ssh/dir")
        assert events == []

    def test_record_and_read_rotation_history(self):
        import shutil

        from general_ludd.security.ssh_key_rotation import (
            RotationEvent,
            record_rotation,
            rotation_history,
        )

        store = tempfile.mkdtemp()
        try:
            event = RotationEvent(
                key_name="prod-key",
                fingerprint="SHA256:abc123",
                old_fingerprints=["SHA256:old1", "SHA256:old2"],
            )
            record_rotation(event, keystore_dir=store)
            events = rotation_history(keystore_dir=store)
            assert len(events) == 1
            assert events[0].key_name == "prod-key"
            assert events[0].fingerprint == "SHA256:abc123"
            assert len(events[0].old_fingerprints) == 2
        finally:
            shutil.rmtree(store, ignore_errors=True)


# ========================================================================
# 11. CAPABILITY LATTICE  —  role dispatch, protected paths, self-modify
# ========================================================================


class TestCapabilityLattice:
    """Per-role capability lattice dispatch checks and self-modification guards."""

    def test_capabilities_for_known_role(self):
        from general_ludd.security.capability_lattice import capabilities_for

        caps = capabilities_for("self_improve_agent")
        assert caps.collections_self_modify is True
        assert "collection" in caps.dispatch_kinds

    def test_capabilities_for_coder_no_collections(self):
        from general_ludd.security.capability_lattice import capabilities_for

        caps = capabilities_for("coder")
        assert caps.collections_self_modify is False
        assert "collection" not in caps.dispatch_kinds

    def test_capabilities_for_unknown_role_deny_all(self):
        from general_ludd.security.capability_lattice import capabilities_for

        caps = capabilities_for("nonexistent_role")
        assert caps.collections_self_modify is False
        assert caps.dispatch_kinds == frozenset()

    def test_role_may_dispatch_allows_granted_kind(self):
        from general_ludd.security.capability_lattice import role_may_dispatch

        assert role_may_dispatch("self_improve_agent", "role") is True
        assert role_may_dispatch("coder", "role") is True

    def test_role_may_dispatch_denies_ungranted_kind(self):
        from general_ludd.security.capability_lattice import role_may_dispatch

        assert role_may_dispatch("coder", "collection") is False
        assert role_may_dispatch("report_status", "role") is False

    def test_role_may_dispatch_denies_unknown_role(self):
        from general_ludd.security.capability_lattice import role_may_dispatch

        assert role_may_dispatch(None, "role") is False
        assert role_may_dispatch("", "role") is False

    def test_is_protected_path_marks_guardrails(self):
        from general_ludd.security.capability_lattice import is_protected_path

        assert is_protected_path("src/.opencode/plugin/enforce-make.ts") is True
        assert is_protected_path(".claude/hooks/no_wait_stop.sh") is True

    def test_is_protected_path_allows_normal_files(self):
        from general_ludd.security.capability_lattice import is_protected_path

        assert is_protected_path("src/general_ludd/daemon.py") is False
        assert is_protected_path("tests/unit/test_auth.py") is False

    def test_check_self_modification_raises_on_protected(self):
        from general_ludd.security.capability_lattice import (
            ProtectedPathError,
            check_self_modification,
        )

        with pytest.raises(ProtectedPathError):
            check_self_modification(".opencode/plugin/enforce-make.ts", "self_improve_agent")

    def test_check_self_modification_allows_normal_path(self):
        from general_ludd.security.capability_lattice import check_self_modification

        check_self_modification("src/general_ludd/daemon.py", "coder")


# ========================================================================
# 12. ADVERSARIAL DETECTOR  —  scanning for adversarial code patterns
# ========================================================================


class TestAdversarialDetector:
    """Adversarial code detector scanning all six pattern categories."""

    def test_detector_initializes_with_all_patterns(self):
        from general_ludd.security.adversarial_detector import (
            AdversarialCodeDetector,
            Category,
        )

        detector = AdversarialCodeDetector()
        categories = detector.get_all_categories()
        assert Category.SELF_SABOTAGE in categories
        assert Category.BACKDOOR in categories
        assert Category.CREDENTIAL_LEAK in categories
        assert Category.LOGIC_DEGRADE in categories
        assert Category.DEPENDENCY_ATTACK in categories
        assert Category.OBFUSCATION in categories

    def test_detect_eval_on_input(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        result = detector.scan_text("eval(request.data)")
        assert len(result.findings) >= 1
        assert any(f.pattern_id == "eval_on_input" for f in result.findings)

    def test_detect_hardcoded_api_key(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        result = detector.scan_text('api_key = "sk-rL8xK9mN2pQ4vW6yA1bC3dE5fG7hI9jK0lM2nO4pQ"')
        assert len(result.findings) >= 1

    def test_detect_os_system_injection(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        result = detector.scan_text('os.system(f"rm -rf {user_input}")')
        assert len(result.findings) >= 1
        assert any(f.pattern_id == "os_system_injection" for f in result.findings)

    def test_detect_base64_exec_obfuscation(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        result = detector.scan_text("eval(base64.b64decode(b'SGVsbG8='))")
        assert len(result.findings) >= 1
        assert any(f.pattern_id == "base64_exec" for f in result.findings)

    def test_clean_code_produces_no_findings(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        result = detector.scan_text("def hello():\n    return 'hello world'\n")
        assert len(result.findings) == 0

    def test_scan_diff_only_flags_additions(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        diff = "+++ b/src/main.py\n- eval(request.data)\n+ print(request.data)\n"
        result = detector.scan_diff(diff)
        assert len(result.findings) == 0

    def test_scan_diff_flags_harmful_additions(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        diff = "+++ b/src/main.py\n+ eval(base64.b64decode(user_input))\n"
        result = detector.scan_diff(diff)
        assert len(result.findings) >= 1

    def test_detector_custom_patterns_extend(self):
        import re

        from general_ludd.security.adversarial_detector import (
            AdversarialCodeDetector,
            AdversarialPattern,
            Category,
            Severity,
        )

        extra = AdversarialPattern(
            id="custom_test_pattern",
            category=Category.BACKDOOR,
            severity=Severity.CRITICAL,
            description="Custom test pattern",
            pattern=re.compile(r"VERY_SPECIFIC_EVIL_FUNCTION\s*\("),
            remediation="Don't call this",
        )
        detector = AdversarialCodeDetector(extra_patterns=[extra])
        result = detector.scan_text("VERY_SPECIFIC_EVIL_FUNCTION(secret_data)")
        assert len(result.findings) >= 1
        assert any(f.pattern_id == "custom_test_pattern" for f in result.findings)

    def test_create_action_intent_from_finding(self):
        from general_ludd.security.adversarial_detector import AdversarialCodeDetector

        detector = AdversarialCodeDetector()
        result = detector.scan_text("eval(request.data)")
        finding = result.findings[0]
        intent = detector.create_action_intent(finding)
        assert intent.action_type == "fix"
        assert len(intent.reason) > 0


# ========================================================================
# 13. PATH CANONICALIZER  —  deny-list markers and path canonicalization
# ========================================================================


class TestPathCanonicalizer:
    """Path canonicalization and deny-list enforcement for protected paths."""

    def test_canonicalize_normalizes_backslashes(self):
        from general_ludd.security.path_canonicalizer import canonicalize_path

        assert canonicalize_path("foo\\bar\\baz.py") == "foo/bar/baz.py"

    def test_canonicalize_lowercases(self):
        from general_ludd.security.path_canonicalizer import canonicalize_path

        assert canonicalize_path("Makefile") == "makefile"

    def test_canonicalize_empty_and_none(self):
        from general_ludd.security.path_canonicalizer import canonicalize_path

        assert canonicalize_path("") == ""
        assert canonicalize_path(None) == ""

    def test_is_denied_path_catches_dot_opencode(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path(".opencode/plugin/enforce-make.ts") is True
        assert is_denied_path("/repo/.opencode/plugin/enforce-make.ts") is True

    def test_is_denied_path_catches_agents_md(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path("AGENTS.md") is True
        assert is_denied_path("docs/agents.md") is True

    def test_is_denied_path_catches_guardrails(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path("guardrails.py") is True
        assert is_denied_path("src/guardrails/stuff.py") is True

    def test_is_denied_path_allows_normal_paths(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path("src/normal/file.py") is False
        assert is_denied_path("tests/unit/test_auth.py") is False

    def test_is_denied_path_segment_exact_makefile(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path("Makefile") is True
        assert is_denied_path("makefile_runner.py") is False

    def test_is_denied_path_segment_exact_alembic(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path("alembic.ini") is True
        assert is_denied_path("my_alembic_wrapper.py") is False

    def test_is_denied_path_handles_empty(self):
        from general_ludd.security.path_canonicalizer import is_denied_path

        assert is_denied_path("") is False
        assert is_denied_path(None) is False
