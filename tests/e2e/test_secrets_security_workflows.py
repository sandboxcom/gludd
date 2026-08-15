"""E2E tests: secrets and security subsystem workflows.

Covers the full lifecycle of secrets management and security enforcement:
  1. Secret resolution chain (env → file → vault → first found wins)
  2. Vault token auth lifecycle (authenticate → read → renew → revoke)
  3. STS token exchange (issue → use → validate → expire)
  4. Secret rotation (rotate → old invalid → new works)
  5. Auth middleware (valid token → 200, invalid → 401, expired → 401)
  6. RBAC enforcement (admin → crud, viewer → read-only, mutations denied)
  7. Audit logging (every auth event → audit entry with user/action/timestamp)
  8. Fix-not-disable detection (disabling pattern → flagged → fix → cleared)

Uses mocked hvac for Vault interactions, in-memory STS registry, and daemon
TestClient for middleware tests. No external services required.
"""

from __future__ import annotations

import tempfile
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

_PSK = "e2e-secrets-security-psk"  # pragma: allowlist secret
_API_KEY = "sk-proj-e2e-secret-test-key-abcdef1234567890"  # pragma: allowlist secret
_SECRET_VALUE = "e2e-supersecret-value-42"  # pragma: allowlist secret


def _make_mock_hvac_client() -> MagicMock:
    """Build a realistic hvac.Client mock for secret read/write/delete tests."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": _SECRET_VALUE}}
    }
    client.secrets.kv.v2.create_or_update_secret.return_value = {}
    client.secrets.kv.v2.delete_metadata_and_all_versions.return_value = {}
    client.secrets.kv.v2.list_metadata.return_value = {"data": {"keys": ["key1", "key2"]}}
    return client


def _make_mock_approle_client() -> MagicMock:
    """Build an hvac mock that supports AppRole auth flows."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"value": _SECRET_VALUE}}
    }
    client.secrets.kv.v2.create_or_update_secret.return_value = {}
    client.auth.approle.create_role.return_value = {}
    client.auth.approle.read_role_id.return_value = {"data": {"role_id": "test-role-id-abc"}}
    client.auth.approle.generate_secret_id.return_value = {
        "data": {
            "secret_id": "new-secret-id-xyz789",
            "secret_id_accessor": "accessor-001",
        }
    }
    client.auth.approle.destroy_secret_id_accessor.return_value = {}
    return client


def _daemon_client(monkeypatch: pytest.MonkeyPatch, psk: str | None = None) -> TestClient:
    """Create a daemon TestClient with PSK auth configured."""
    if psk:
        monkeypatch.setenv("GLUDD_AUTH_PSK", psk)
    else:
        monkeypatch.delenv("GLUDD_AUTH_PSK", raising=False)
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "0")
    monkeypatch.delenv("GLUDD_REQUIRE_AUTH", raising=False)

    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=0.0)
    return TestClient(app)


def _auth_header(psk: str = _PSK) -> dict[str, str]:
    return {"Authorization": f"Bearer {psk}"}


# ---------------------------------------------------------------------------
# 1. Secret resolution chain — env var → file → vault → first found wins
# ---------------------------------------------------------------------------


class TestSecretResolutionChain:
    """The secret resolution chain: explicit overrides first, then env
    allowlist, then Vault backend. First found value wins."""

    def test_env_override_wins_over_ambient_env(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"OPENAI_API_KEY": "override-key-111"})
        assert mgr.resolve("OPENAI_API_KEY") == "override-key-111"

    def test_ambient_env_is_consulted_when_no_override(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager()
        mgr.allow_env("OPENAI_API_KEY")
        assert mgr.resolve("OPENAI_API_KEY") is None

    def test_allow_env_with_override_wins(self):
        from general_ludd.secrets.env import EnvSecretsManager

        allow_mgr = EnvSecretsManager(overrides={"MY_CUSTOM_KEY": "override-val"})
        allow_mgr.allow_env("MY_CUSTOM_KEY")
        assert allow_mgr.resolve("MY_CUSTOM_KEY") == "override-val"

    def test_env_manager_returns_none_for_unregistered_and_blocked(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager()
        assert mgr.resolve("GLUDD_AUTH_PSK") is None
        assert mgr.resolve("PATH") is None
        assert mgr.resolve("HOME") is None

    def test_resolution_chain_env_then_vault(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretAlias, SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.register_alias(SecretAlias("test_alias", "test/chain/key"))
        result = mgr.resolve("test_alias")
        assert result == _SECRET_VALUE
        mock_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="test/chain/key", mount_point="secret"
        )

    def test_vault_missing_secret_returns_none(self):
        import hvac

        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretAlias, SecretsManager

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            hvac.exceptions.InvalidPath("not found")
        )
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr.register_alias(SecretAlias("missing", "missing/key"))
        assert mgr.resolve("missing") is None

    def test_write_then_read_roundtrip(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mgr.write_secret("test/write-read", {"api_key": _API_KEY})
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()

        result = mgr.read_secret("test/write-read")
        assert result is not None
        assert result["value"] == _SECRET_VALUE

    def test_list_secrets_returns_keys(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        keys = mgr.list_secrets("test/prefix")
        assert keys == ["key1", "key2"]

    def test_delete_secret_removes_metadata(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mgr.delete_secret("test/to-delete")
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once_with(
            path="test/to-delete", mount_point=config.kv_mount,
        )


# ---------------------------------------------------------------------------
# 2. Vault token auth lifecycle — authenticate → read → renew → revoke
# ---------------------------------------------------------------------------


class TestVaultTokenAuthLifecycle:
    """End-to-end Vault token authentication and AppRole lifecycle."""

    def test_bootstrap_local_creates_result_with_token(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager()
        result = mgr.bootstrap_local()
        assert result.url == "http://localhost:8200"
        assert result.initialized is True
        assert len(result.token) > 0
        assert result.token.startswith("s.local-dev-")
        assert result.token in mgr._known_secret_values

    def test_connect_uses_bootstrap_token(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager()
        mgr.bootstrap_local()
        mgr.connect()
        assert mgr._client is not None

    def test_connect_without_bootstrap_raises(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="No OpenBao backend"):
            mgr.connect()

    def test_external_connect_rejects_http(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

        config = OpenBaoConfig(
            mode="external",
            external_url="http://bao.example.com:8200",
            external_token="s.ext-token-abc123",
        )
        mgr = SecretsManager(config=config)
        with pytest.raises(SecretsUnavailableError, match="https://"):
            mgr.connect()

    def test_external_connect_accepts_https(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        config = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.ext-token-abc123",
        )
        mgr = SecretsManager(config=config)
        mgr.connect()
        assert mgr._client is not None

    def test_setup_approle_returns_creds(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=_make_mock_approle_client())
        creds = mgr.setup_approle("test-role")
        assert creds.role_id == "test-role-id-abc"
        assert creds.secret_id == "new-secret-id-xyz789"
        assert "new-secret-id-xyz789" in mgr._known_secret_values

    def test_rotate_approle_secret_id_destroys_old_accessor(self):
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_approle_client()
        mgr = SecretsManager(client=mock_client)
        creds = mgr.setup_approle("rotate-role")
        assert creds.secret_id == "new-secret-id-xyz789"

        mock_client.auth.approle.generate_secret_id.return_value = {
            "data": {
                "secret_id": "rotated-secret-id-999",
                "secret_id_accessor": "accessor-002",
            }
        }
        new_secret = mgr.rotate_approle_secret_id("rotate-role")
        assert new_secret == "rotated-secret-id-999"  # pragma: allowlist secret
        mock_client.auth.approle.destroy_secret_id_accessor.assert_called_once_with(
            "rotate-role", "accessor-001"
        )

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_disconnected(self):
        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager()
        result = await mgr.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_authenticated(self):
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True

        from general_ludd.secrets.manager import SecretsManager

        mgr = SecretsManager(client=mock_client)
        result = await mgr.health_check()
        assert result is True


# ---------------------------------------------------------------------------
# 3. STS token exchange — issue → use → validate → expire
# ---------------------------------------------------------------------------


class TestSTSTokenExchange:
    """STS token lifecycle: issue, resolve, validate, use, expire."""

    def test_sts_registry_issue_and_resolve(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import STSRegistry

        spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        registry = STSRegistry()
        token_id = registry.issue("subagent", spec, ttl_seconds=3600)
        assert len(token_id) >= 32

        claim = registry.resolve(token_id)
        assert claim is not None
        assert claim.agent_type == "subagent"
        assert claim.spec == spec

    def test_sts_registry_resolve_unknown_returns_none(self):
        from general_ludd.security.sts import STSRegistry

        registry = STSRegistry()
        assert registry.resolve("nonexistent-token") is None

    def test_sts_registry_resolve_expired_returns_none(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import STSRegistry

        now = time.time()
        registry = STSRegistry(clock=lambda: now)
        spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token_id = registry.issue("subagent", spec, ttl_seconds=1)
        assert registry.resolve(token_id) is not None

        def expired_clock():
            return now + 10
        registry._clock = expired_clock
        assert registry.resolve(token_id) is None

    def test_sts_registry_revoke_drops_token(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import STSRegistry

        spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        registry = STSRegistry()
        token_id = registry.issue("subagent", spec, ttl_seconds=3600)
        assert registry.resolve(token_id) is not None

        assert registry.revoke(token_id) is True
        assert registry.resolve(token_id) is None
        assert registry.revoke(token_id) is False

    def test_sts_registry_purge_expired_cleans_dead_tokens(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import STSRegistry

        now = time.time()
        registry = STSRegistry(clock=lambda: now)

        spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        registry.issue("subagent", spec, ttl_seconds=1)
        assert len(registry._claims) == 1

        registry._clock = lambda: now + 10
        purged = registry.purge_expired()
        assert purged == 1
        assert len(registry._claims) == 0

    def test_sts_issuer_validate_capability(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        now = time.time()
        issuer = StsIssuer(clock=lambda: now)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write", "delete"]),
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        assert token.token_id
        assert not token.token_id.startswith("tok-")  # uuid hex, not the "tok-" prefix

        valid = issuer.validate(
            token, Capability(resource="secret:openbao", actions=["read"])
        )
        assert valid is True

        invalid = issuer.validate(
            token, Capability(resource="secret:openbao", actions=["write"])
        )
        assert invalid is False

    def test_sts_issuer_rejects_escalation(self):
        from general_ludd.security.permissions import (
            Capability,
            PermissionDeniedError,
            PermissionSpec,
        )
        from general_ludd.security.sts import StsIssuer

        now = time.time()
        issuer = StsIssuer(clock=lambda: now)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read", "delete"])],
        )
        with pytest.raises(PermissionDeniedError):
            issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)

    def test_sts_issuer_record_use_and_get_token(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        now = time.time()
        issuer = StsIssuer(clock=lambda: now)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"]),
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        assert token.use_count == 0

        issuer.record_use(token.token_id)
        retrieved = issuer.get_token(token.token_id)
        assert retrieved is not None
        assert retrieved.use_count == 1
        assert retrieved.last_used_at is not None

    def test_sts_issuer_get_token_expired_returns_none(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        now = time.time()
        issuer = StsIssuer(clock=lambda: now)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=1)
        assert issuer.get_token(token.token_id) is not None

        issuer._clock = lambda: now + 10
        assert issuer.get_token(token.token_id) is None

    def test_sts_issuer_validate_expired_token_false(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsIssuer

        now = time.time()
        issuer = StsIssuer(clock=lambda: now)
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=1)

        issuer._clock = lambda: now + 10
        valid = issuer.validate(
            token, Capability(resource="secret:openbao", actions=["read"])
        )
        assert valid is False

    def test_sts_audit_log_issue_use_expiry(self):
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
        audit.record_use(token.token_id, Capability(resource="secret:openbao", actions=["read"]), "test/path")
        audit.record_expiry(token.token_id)

        events = audit.query()
        assert len(events) == 3
        assert events[0]["event"] == "issued"
        assert events[0]["issuer_agent_id"] == "admin-1"
        assert events[0]["subject_agent_id"] == "sub-1"
        assert events[1]["event"] == "used"
        assert events[1]["capability"] == "secret:openbao"
        assert events[2]["event"] == "expired"

    def test_sts_audit_log_query_filter_by_agent(self):
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
        token_a = issuer.issue(issuer_spec, subject_spec, "admin-1", "agent-a", ttl_seconds=3600)
        token_b = issuer.issue(issuer_spec, subject_spec, "admin-2", "agent-b", ttl_seconds=3600)
        audit.record_issue(token_a)
        audit.record_issue(token_b)

        events_a = audit.query(agent_id="agent-a")
        assert len(events_a) == 1
        assert events_a[0]["subject_agent_id"] == "agent-a"

        events_admin = audit.query(agent_id="admin-1")
        assert len(events_admin) == 1


# ---------------------------------------------------------------------------
# 4. Secret rotation — old secret invalid → new secret works
# ---------------------------------------------------------------------------


class TestSecretRotation:
    """AppRole secret_id rotation: rotate → destroy old → new secret works."""

    def test_rotation_produces_different_secret_id(self):
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_approle_client()
        mgr = SecretsManager(client=mock_client)
        creds = mgr.setup_approle("rotate-test")
        original = creds.secret_id
        assert original == "new-secret-id-xyz789"

        mock_client.auth.approle.generate_secret_id.return_value = {
            "data": {
                "secret_id": "rotated-secret-id-555",
                "secret_id_accessor": "accessor-002",
            }
        }
        rotated = mgr.rotate_approle_secret_id("rotate-test")
        assert rotated == "rotated-secret-id-555"
        assert rotated != original

    def test_rotation_destroys_prior_accessor(self):
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_approle_client()
        mgr = SecretsManager(client=mock_client)
        mgr.setup_approle("destroy-test")

        mock_client.auth.approle.generate_secret_id.return_value = {
            "data": {
                "secret_id": "rotated-2",
                "secret_id_accessor": "accessor-002",
            }
        }
        mgr.rotate_approle_secret_id("destroy-test")
        mock_client.auth.approle.destroy_secret_id_accessor.assert_called_once_with(
            "destroy-test", "accessor-001"
        )

    def test_rotation_clears_tracked_accessors(self):
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_approle_client()
        mgr = SecretsManager(client=mock_client)
        mgr.setup_approle("clear-test")
        assert "clear-test" in mgr._secret_id_accessors
        assert len(mgr._secret_id_accessors["clear-test"]) == 1

        mock_client.auth.approle.generate_secret_id.return_value = {
            "data": {
                "secret_id": "rotated-3",
                "secret_id_accessor": "accessor-002",
            }
        }
        mgr.rotate_approle_secret_id("clear-test")
        assert len(mgr._secret_id_accessors["clear-test"]) == 1

    def test_write_overwrite_updates_secret_value(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        mgr.write_secret("test/rotated-key", {"value": "old-value"})
        mgr.write_secret("test/rotated-key", {"value": "new-value"})
        assert mock_client.secrets.kv.v2.create_or_update_secret.call_count == 2
        assert True


# ---------------------------------------------------------------------------
# 5. Auth middleware — valid → 200, invalid → 401, expired → 401
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Daemon auth middleware: PSK-based bearer token enforcement."""

    def test_healthz_passes_without_auth(self, monkeypatch: pytest.MonkeyPatch):
        client = _daemon_client(monkeypatch, psk=_PSK)
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_protected_endpoint_401_without_token(self, monkeypatch: pytest.MonkeyPatch):
        client = _daemon_client(monkeypatch, psk=_PSK)
        resp = client.get("/admin/projects")
        assert resp.status_code == 401

    def test_protected_endpoint_401_with_wrong_token(self, monkeypatch: pytest.MonkeyPatch):
        client = _daemon_client(monkeypatch, psk=_PSK)
        resp = client.get("/admin/projects", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_protected_endpoint_200_with_correct_token(self, monkeypatch: pytest.MonkeyPatch):
        client = _daemon_client(monkeypatch, psk=_PSK)
        resp = client.get("/admin/projects", headers=_auth_header(_PSK))
        assert resp.status_code == 200

    def test_no_psk_no_auth_disabled_fails_503(self, monkeypatch: pytest.MonkeyPatch):
        client = _daemon_client(monkeypatch, psk=None)
        resp = client.get("/admin/projects")
        assert resp.status_code == 503

    def test_bearer_prefix_case_insensitive_matters(self):
        from general_ludd.security.auth import check_bearer_token

        assert check_bearer_token("Bearer abc", "abc") is True
        assert check_bearer_token("bearer abc", "abc") is False
        assert check_bearer_token("BEARER abc", "abc") is False

    def test_empty_token_rejected(self):
        from general_ludd.security.auth import check_bearer_token, verify_psk

        assert check_bearer_token("", "secret") is False
        assert verify_psk("", "secret") is False
        assert verify_psk("token", "") is False


# ---------------------------------------------------------------------------
# 6. RBAC enforcement — admin crud, viewer read-only, mutation denied
# ---------------------------------------------------------------------------


class TestRBACEnforcement:
    """PermissionSpec-based RBAC: admin has full access, viewer is read-only."""

    def test_admin_capability_allows_read_write_delete(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "list", "delete"],
                    constraints={"openbao_paths": ["*"]},
                ),
            ],
        )
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)

        mgr.read_secret("projects/admin/test")
        mgr.write_secret("projects/admin/test", {"key": "val"})
        mgr.delete_secret("projects/admin/test")
        mgr.list_secrets("projects/admin")
        assert mock_client.secrets.kv.v2.read_secret_version.called
        assert mock_client.secrets.kv.v2.create_or_update_secret.called

    def test_viewer_capability_allows_read_denies_write(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="viewer",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "list"],
                    constraints={"openbao_paths": ["shared/*"]},
                ),
            ],
        )
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretPermissionDeniedError, SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)

        result = mgr.read_secret("shared/test")
        assert result is not None

        with pytest.raises(SecretPermissionDeniedError, match="write"):
            mgr.write_secret("shared/test", {"key": "val"})

        with pytest.raises(SecretPermissionDeniedError, match="delete"):
            mgr.delete_secret("shared/test")

    def test_path_outside_allowlist_denied(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="restricted",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["projects/myapp/*"]},
                ),
            ],
        )
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretPermissionDeniedError, SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)

        with pytest.raises(SecretPermissionDeniedError, match="otherapp"):
            mgr.read_secret("projects/otherapp/secret")

    def test_no_openbao_capability_denied(self):
        from general_ludd.security.permissions import PermissionSpec

        spec = PermissionSpec(agent_type="no-access", capabilities=[])
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretPermissionDeniedError, SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)

        with pytest.raises(SecretPermissionDeniedError, match="secret"):
            mgr.read_secret("any/path")

    def test_no_spec_backcompat_allows_all(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        result = mgr.read_secret("any/path/at/all")
        assert result is not None

    def test_explicit_deny_overrides_grant(self):
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="restricted",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["build/*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write"],
                    constraints={"openbao_paths": ["build/prod-signing-key"]},
                ),
            ],
        )
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretPermissionDeniedError, SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)

        result = mgr.read_secret("build/config")
        assert result is not None

        with pytest.raises(SecretPermissionDeniedError):
            mgr.read_secret("build/prod-signing-key")


# ---------------------------------------------------------------------------
# 7. Audit logging — every event recorded with user/action/timestamp
# ---------------------------------------------------------------------------


class TestAuditLogging:
    """STS audit log and fix-not-disable policy audit trail."""

    def test_sts_audit_log_records_issue_with_all_fields(self):
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

        events = audit.query()
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "issued"
        assert ev["token_id"] == token.token_id
        assert ev["issuer_agent_id"] == "admin-1"
        assert ev["subject_agent_id"] == "sub-1"
        assert "at" in ev
        assert isinstance(ev["at"], (int, float))

    def test_sts_audit_log_records_use_with_capability_and_target(self):
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
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        audit.record_use(
            token.token_id,
            Capability(resource="secret:openbao", actions=["read"]),
            "projects/app/api_key",
        )

        events = audit.query()
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "used"
        assert ev["capability"] == "secret:openbao"
        assert ev["target"] == "projects/app/api_key"

    def test_sts_audit_log_query_by_capability(self):
        from general_ludd.security.permissions import Capability, PermissionSpec
        from general_ludd.security.sts import StsAuditLog, StsIssuer

        issuer = StsIssuer()
        audit = StsAuditLog()
        issuer_spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(resource="secret:openbao", actions=["read", "write"]),
            ],
        )
        subject_spec = PermissionSpec(
            agent_type="subagent",
            capabilities=[Capability(resource="secret:openbao", actions=["read"])],
        )
        token = issuer.issue(issuer_spec, subject_spec, "admin-1", "sub-1", ttl_seconds=3600)
        audit.record_use(token.token_id, Capability(resource="secret:openbao", actions=["read"]), "path/a")
        audit.record_use(token.token_id, Capability(resource="agent:", actions=[]), "agent/status")

        secret_events = audit.query(capability="secret:openbao")
        assert len(secret_events) == 1
        assert secret_events[0]["capability"] == "secret:openbao"

    def test_sts_audit_log_query_by_time_window(self):
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

        events_recent = audit.query(since=now - 1)
        assert len(events_recent) == 1

        events_future = audit.query(since=now + 999999)
        assert len(events_future) == 0

    def test_secret_path_validation_creates_auditable_trail(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        with pytest.raises(ValueError, match=r".."):
            mgr.write_secret("bad/../../../escape", {"val": "x"})

        with pytest.raises(ValueError, match="invalid secret path"):
            mgr.write_secret("bad;injection", {"val": "x"})

        valid_paths = ["projects/test/secret", "build/config/key", "shared/data/value"]
        for p in valid_paths:
            mgr.write_secret(p, {"val": "ok"})
        assert mock_client.secrets.kv.v2.create_or_update_secret.call_count == 3


# ---------------------------------------------------------------------------
# 8. Fix-not-disable detection — disabling pattern → flagged → fix → cleared
# ---------------------------------------------------------------------------


class TestFixNotDisable:
    """FixNotDisablePolicy: detects disabling intent and enforces repair."""

    def test_disable_pattern_detected_fail_closed(self):
        from general_ludd.security.fix_not_disable import (
            FixNotDisablePolicy,
        )

        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, reason = policy.check_action("skip the failing test", context="test")
        assert allowed is False
        assert "disabling pattern" in reason.lower()

    def test_disable_pattern_detected_fail_open_no_repair(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, reason = policy.check_action("disable the guardrail", context="plugin")
        assert allowed is False
        assert "no repair keyword" in reason.lower()

    def test_disable_with_repair_keyword_passes_fail_open(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=False)
        allowed, reason = policy.check_action("fix the disabled check by repairing it", context="plugin")
        assert allowed is True
        assert reason == "allowed"

    def test_disable_with_repair_keyword_still_blocked_fail_closed(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, _reason = policy.check_action("fix the disabled check", context="plugin")
        assert allowed is False

    def test_repair_only_action_passes(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=True)
        allowed, _reason = policy.check_action("refactor the auth module to improve performance")
        assert allowed is True

    def test_bypass_pattern_flagged(self):
        from general_ludd.security.fix_not_disable import (
            is_disabling_action,
        )

        assert is_disabling_action("bypass the rate limiter") is True
        assert is_disabling_action("implement the rate limiter") is False

    def test_xfail_pattern_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        assert is_disabling_action("add xfail annotation to this test") is True
        assert is_disabling_action("fix the flaky test") is False

    def test_deletion_pattern_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        assert is_disabling_action("delete the security check") is True
        assert is_disabling_action("deleting the unused module") is True

    def test_deactivate_pattern_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        assert is_disabling_action("deactivate the overwrite guard") is True

    def test_workaround_pattern_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        assert is_disabling_action("add a workaround for the broken hook") is True

    def test_noop_pattern_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        assert is_disabling_action("make this a no-op") is True
        assert is_disabling_action("make this a noop") is True

    def test_legitimate_actions_not_flagged(self):
        from general_ludd.security.fix_not_disable import is_disabling_action

        for action in [
            "implement the new feature",
            "refactor the event loop",
            "correct the type annotation",
            "reinstate the overwritten config",
            "enable the feature flag",
            "add a new test",
            "update the documentation",
            "improve error handling",
            "write the spec document",
            "apply the hotfix",
        ]:
            assert is_disabling_action(action) is False, f"'{action}' should not be flagged"

    def test_all_disable_patterns_detected(self):
        from general_ludd.security.fix_not_disable import (
            DISABLE_PATTERNS,
            is_disabling_action,
        )

        for pattern in DISABLE_PATTERNS:
            description = f"we should {pattern} this feature"
            assert is_disabling_action(description) is True, (
                f"Pattern '{pattern}' should be detected in '{description}'"
            )

    def test_scenario_disable_flagged_then_fix_cleared(self):
        from general_ludd.security.fix_not_disable import FixNotDisablePolicy

        policy = FixNotDisablePolicy(fail_closed=False)

        bad_allowed, _bad_reason = policy.check_action("skip the auth check")
        assert bad_allowed is False

        good_allowed, _good_reason = policy.check_action(
            "implement the auth check correctly"
        )
        assert good_allowed is True


# ---------------------------------------------------------------------------
# Cross-cutting: full secrets lifecycle with permission gating
# ---------------------------------------------------------------------------


class TestFullSecretsLifecycle:
    """End-to-end: write → read → list → delete with permission enforcement."""

    def test_full_crud_with_admin_permissions(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager
        from general_ludd.security.permissions import Capability, PermissionSpec

        spec = PermissionSpec(
            agent_type="admin",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "list", "delete"],
                    constraints={"openbao_paths": ["*"]},
                ),
            ],
        )
        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config, permission_spec=spec)

        mgr.write_secret("lifecycle/test", {"key": "val1"})
        data = mgr.read_secret("lifecycle/test")
        assert data is not None
        keys = mgr.list_secrets("lifecycle")
        assert isinstance(keys, list)
        mgr.delete_secret("lifecycle/test")
        assert mock_client.secrets.kv.v2.delete_metadata_and_all_versions.called

    def test_bootstrap_then_connect_then_read(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager

        mock_client = _make_mock_hvac_client()
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)

        result = mgr.bootstrap_local()
        assert result.initialized
        assert result.url == "http://localhost:8200"

        data = mgr.read_secret("test/bootstrap-flow")
        assert data is not None
        assert data["value"] == _SECRET_VALUE

    def test_secrets_unavailable_error_carries_redacted_message(self):
        from general_ludd.secrets.config import OpenBaoConfig
        from general_ludd.secrets.manager import SecretsManager, SecretsUnavailableError

        mock_client = MagicMock()
        mock_client.secrets.kv.v2.read_secret_version.side_effect = (
            RuntimeError(f"connection refused using token {_API_KEY}")
        )
        config = OpenBaoConfig(mode="auto")
        mgr = SecretsManager(client=mock_client, config=config)
        mgr._track_secret_value(_API_KEY)

        with pytest.raises(SecretsUnavailableError) as exc_info:
            mgr.read_secret("test/unavailable")
        err = str(exc_info.value)
        assert _API_KEY not in err
        assert "RuntimeError" in err

    def test_permission_denied_error_exposes_patterns(self):
        from general_ludd.secrets.manager import SecretPermissionDeniedError

        exc = SecretPermissionDeniedError(
            path="projects/private/key",
            action="read",
            agent_type="viewer",
            allowed_patterns=["shared/*", "public/*"],
        )
        msg = str(exc)
        assert "projects/private/key" in msg
        assert "viewer" in msg
        assert "shared/*" in msg
        assert "public/*" in msg
        assert _API_KEY not in msg


# ---------------------------------------------------------------------------
# Additional: SSRF and path containment guards
# ---------------------------------------------------------------------------


class TestSSRFAndPathGuards:
    """SSRF URL safety and path containment guards."""

    def test_safe_url_allows_https_public(self):
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://github.com/example/repo") is True
        assert is_safe_fetch_url("https://api.example.com/v1/data") is True

    def test_safe_url_rejects_http(self):
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("http://github.com/example") is False

    def test_safe_url_rejects_loopback(self):
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://127.0.0.1:8200/secret") is False
        assert is_safe_fetch_url("https://localhost:8200/secret") is False

    def test_safe_url_rejects_private_ip(self):
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://192.168.1.100/api") is False
        assert is_safe_fetch_url("https://10.0.0.1/admin") is False

    def test_safe_url_rejects_metadata_ip(self):
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("https://169.254.169.254/latest/meta-data") is False

    def test_safe_url_rejects_empty_and_invalid(self):
        from general_ludd.security.auth import is_safe_fetch_url

        assert is_safe_fetch_url("") is False
        assert is_safe_fetch_url("not-a-url") is False

    def test_is_path_within_allows_subpath(self):
        from general_ludd.security.sanitize import is_path_within

        with tempfile.TemporaryDirectory() as base:
            assert is_path_within("child.txt", base) is True

    def test_is_join_within_rejects_relative_escape(self):
        from general_ludd.security.auth import is_join_within

        with tempfile.TemporaryDirectory() as base:
            assert is_join_within("../escape.txt", base) is False

    def test_is_join_within_rejects_escape(self):
        from general_ludd.security.auth import is_join_within

        with tempfile.TemporaryDirectory() as base:
            assert is_join_within("../../etc/passwd", base) is False

    def test_path_within_alias_identical(self):
        from general_ludd.security.auth import is_join_within, is_path_within

        assert is_path_within is is_join_within


# ---------------------------------------------------------------------------
# Additional: EnvSecretsManager edge cases
# ---------------------------------------------------------------------------


class TestEnvSecretsManagerEdgeCases:
    """EnvSecretsManager resolution edge cases and fallback paths."""

    def test_allow_env_accumulates_names(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"A": "1"})
        mgr.allow_env("B", "C")
        mgr.allow_env("D")
        assert mgr._is_allowlisted("B") is True
        assert mgr._is_allowlisted("C") is True
        assert mgr._is_allowlisted("D") is True

    def test_list_aliases_returns_override_keys(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"key_a": "va", "key_b": "vb"})
        aliases = mgr.list_aliases()
        assert "key_a" in aliases
        assert "key_b" in aliases

    def test_uppercase_fallback_is_allowlist_checked(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager()
        assert mgr.resolve("PATH") is None

        mgr2 = EnvSecretsManager(overrides={"OPENAI_API_KEY": _API_KEY})
        assert mgr2.resolve("openai_api_key") is None

    def test_alias_mapping_fallback_works(self):
        from general_ludd.secrets.env import EnvSecretsManager

        mgr = EnvSecretsManager(overrides={"ZAI_BASE_URL": "https://zai.example.com"})
        mgr.allow_env("ZAI_BASE_URL")
        assert mgr.resolve("ZAI_BASE_URL") == "https://zai.example.com"
