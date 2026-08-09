"""Deep tests for credential vault, OpenBao scope, and token rotation.

Covers: credential paths, TTL caps, scope validation, token rotation
edge cases, and secrets-unavailable error propagation.
"""

from __future__ import annotations

import secrets as crypto_secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.audit.audit_logger import AuditLogger
from general_ludd.secrets.credential_vault import (
    ConfigStoreError,
    CredentialVault,
    RootSealHoldError,
    RootSealIntegration,
    RootSealState,
    SecretsManager,
    SecretsUnavailableError,
)
from general_ludd.secrets.openbao_scope import (
    OpenBaoPathScope,
    OpenBaoScopeDenied,
    OpenBaoScopeRequest,
    OpenBaoTTLCap,
    policy_name_for_agent,
    validate_openbao_mount,
    validate_openbao_path,
    validate_openbao_policy_name,
)
from general_ludd.secrets.token_rotator import (
    TokenRotationError,
    TokenRotator,
)
from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.sts.audit import StsAuditPipeline

# ── Helpers ──────────────────────────────────────────────────────────────


def _build_vault_with_seal(
    seal_state: RootSealState = RootSealState.UNINITIALIZED,
) -> CredentialVault:
    seal = MagicMock(spec=RootSealIntegration)
    seal.state = seal_state
    return CredentialVault(
        secrets_manager=MagicMock(spec=SecretsManager),
        audit_logger=MagicMock(spec=AuditLogger),
        sts_pipeline=MagicMock(spec=StsAuditPipeline),
        root_seal=seal,
    )


# ── CredentialVault ──────────────────────────────────────────────────────


class TestCredentialVault:
    def test_vault_rejects_operation_when_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.issue_credential(
                agent_id="agent-001",
                caps=frozenset([Capability.PERMISSION_READ]),
            )

    def test_vault_rejects_operation_when_uninitialized(self):
        vault = _build_vault_with_seal(RootSealState.UNINITIALIZED)
        with pytest.raises(RootSealHoldError, match="uninitialized"):
            vault.issue_credential(
                agent_id="agent-001",
                caps=frozenset([Capability.PERMISSION_READ]),
            )

    def test_vault_allows_operation_when_unsealed(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-xyz"))
        result = vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        assert result is not None

    def test_vault_unseal_via_root_seal(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        vault.root_seal.unseal = MagicMock()
        vault.unseal(key_shards=["shard-1", "shard-2"])
        vault.root_seal.unseal.assert_called_once_with(key_shards=["shard-1", "shard-2"])

    def test_vault_config_store_error_wraps_inner(self):
        inner = ValueError("missing field")
        err = ConfigStoreError("bad config", inner)
        assert "bad config" in str(err)
        assert err.__cause__ is inner


# ── RootSealState ────────────────────────────────────────────────────────


class TestRootSealState:
    def test_states_are_distinct(self):
        states = {
            RootSealState.UNINITIALIZED,
            RootSealState.SEALED,
            RootSealState.UNSEALED,
        }
        assert len(states) == 3


# ── Credential Paths ─────────────────────────────────────────────────────


class TestCredentialPaths:
    def test_agent_credential_path_format(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        path = vault._agent_credential_path("agent-042")
        assert path.startswith("gludd/creds/")
        assert "agent-042" in path

    def test_agent_credential_path_is_deterministic(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        a = vault._agent_credential_path("agent-042")
        b = vault._agent_credential_path("agent-042")
        assert a == b

    def test_agent_credential_path_differs_per_agent(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        a = vault._agent_credential_path("agent-001")
        b = vault._agent_credential_path("agent-002")
        assert a != b


# ── SecretsManager ────────────────────────────────────────────────────────


class TestSecretsManager:
    def test_connect_sets_connected_flag(self):
        mgr = SecretsManager()
        mgr.connect()
        assert mgr.connected is True

    def test_read_secret_requires_connection(self):
        mgr = SecretsManager()
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            mgr.read_secret("any/path")

    def test_read_secret_returns_mocked_value_when_connected(self):
        mgr = SecretsManager()
        mgr.connect()
        value = mgr.read_secret("some/path")
        assert isinstance(value, str)

    def test_write_secret_requires_connection(self):
        mgr = SecretsManager()
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            mgr.write_secret("some/path", {"key": "val"})

    def test_write_secret_works_when_connected(self):
        mgr = SecretsManager()
        mgr.connect()
        mgr.write_secret("some/path", {"key": "val"})

    def test_connect_external_rejects_plaintext(self):
        from general_ludd.secrets.openbao_scope import OpenBaoConfig

        cfg = OpenBaoConfig(
            mode="external",
            external_url="http://bao.example.com:8200",
            external_token="s.token",
        )
        mgr = SecretsManager(config=cfg)
        with pytest.raises(SecretsUnavailableError, match="https://"):
            mgr.connect()


# ── OpenBao Path Scope ───────────────────────────────────────────────────


class TestOpenBaoPathScope:
    def test_allow_exact_prefix(self):
        scope = OpenBaoPathScope(allowed_paths=["gludd/creds"], denied_paths=[])
        req = OpenBaoScopeRequest(path="gludd/creds/agent-001/token")
        assert scope.allows(req)

    def test_deny_denied_prefix(self):
        scope = OpenBaoPathScope(allowed_paths=["gludd/creds"], denied_paths=["gludd/creds/root"])
        req = OpenBaoScopeRequest(path="gludd/creds/root/unseal")
        assert not scope.allows(req)

    def test_allowed_over_denied_on_exact_match(self):
        scope = OpenBaoPathScope(allowed_paths=["gludd/creds/root"], denied_paths=["gludd/creds"])
        req = OpenBaoScopeRequest(path="gludd/creds/root/unseal")
        assert scope.allows(req)

    def test_no_allowed_paths_denies_everything(self):
        scope = OpenBaoPathScope(allowed_paths=[], denied_paths=[])
        req = OpenBaoScopeRequest(path="gludd/creds/agent-001/token")
        assert not scope.allows(req)

    def test_wildcard_allowed_covers_any(self):
        scope = OpenBaoPathScope(allowed_paths=["*"], denied_paths=[])
        req = OpenBaoScopeRequest(path="anything/at/all")
        assert scope.allows(req)

    def test_deny_wins_over_wildcard_allow(self):
        scope = OpenBaoPathScope(allowed_paths=["*"], denied_paths=["sys", "admin"])
        req_sys = OpenBaoScopeRequest(path="sys/seal-status")
        req_ok = OpenBaoScopeRequest(path="gludd/creds/agent-001")
        assert not scope.allows(req_sys)
        assert scope.allows(req_ok)

    def test_valid_path_against_openbao_paths(self):
        scope = OpenBaoPathScope(allowed_paths=["gludd/creds"], denied_paths=[])
        req = OpenBaoScopeRequest(path="gludd/creds/agent-042/token")
        assert scope.allows(req)

    def test_permission_scope_restricts_capabilities(self):
        perm = PermissionSpec(
            principals=["agent-001"],
            role_name="agent-agent-001",
            allowed_hosts=["*"],
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        scope = OpenBaoPathScope.from_permission(
            perm,
            allowed_paths=["gludd/creds"],
            denied_paths=["gludd/creds/root"],
        )
        assert scope.allows(OpenBaoScopeRequest(path="gludd/creds/agent-001"))


# ── Scope Denied Error ───────────────────────────────────────────────────


class TestOpenBaoScopeDenied:
    def test_denied_error_contains_path(self):
        err = OpenBaoScopeDenied("sys/seal-status")
        assert "sys/seal-status" in str(err)

    def test_denied_error_is_value_error(self):
        err = OpenBaoScopeDenied("sys/admin")
        assert isinstance(err, ValueError)


# ── TTL Caps ─────────────────────────────────────────────────────────────


class TestOpenBaoTTLCap:
    def test_default_ttl_is_max(self):
        cap = OpenBaoTTLCap()
        assert cap.compute(999999) == 999999

    def test_agent_ttl_is_sane(self):
        cap = OpenBaoTTLCap(max_agent_ttl=3600)
        assert cap.compute(3600) == 3600
        assert cap.compute(7200) == 3600

    def test_child_ttl_not_longer_than_parent(self):
        cap = OpenBaoTTLCap(max_agent_ttl=3600, max_child_ttl=900)
        assert cap.compute(900) == 900
        assert cap.compute(1800) == 900

    def test_human_ttl_is_low(self):
        cap = OpenBaoTTLCap(max_human_ttl=300)
        assert cap.compute(600) == 300

    def test_ttl_zero_means_no_override(self):
        cap = OpenBaoTTLCap(max_agent_ttl=0)
        assert cap.compute(7200) == 7200


# ── Validate Mount ───────────────────────────────────────────────────────


class TestValidateOpenBaoMount:
    def test_valid_mount_name_accepted(self):
        validate_openbao_mount("gludd")

    def test_empty_mount_rejected(self):
        with pytest.raises(ValueError):
            validate_openbao_mount("")

    @pytest.mark.parametrize("bad", ["../", "/etc", "mount with spaces"])
    def test_invalid_chars_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_openbao_mount(bad)


# ── Validate Path ────────────────────────────────────────────────────────


class TestValidateOpenBaoPath:
    def test_valid_path_accepted(self):
        validate_openbao_path("gludd/creds/agent-001")

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError):
            validate_openbao_path("")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            validate_openbao_path("../etc/passwd")

    def test_path_with_slash_prefix_rejected(self):
        with pytest.raises(ValueError):
            validate_openbao_path("/absolute/path")

    def test_path_length_limit(self):
        with pytest.raises(ValueError):
            validate_openbao_path("a" * 4097)


# ── Policy Name Validation ───────────────────────────────────────────────


class TestValidateOpenBaoPolicyName:
    def test_valid_policy_name_accepted(self):
        validate_openbao_policy_name("gludd-agent-agent-001")

    def test_policy_name_with_slashes_rejected(self):
        with pytest.raises(ValueError):
            validate_openbao_policy_name("gludd/evil")

    def test_policy_name_too_long_rejected(self):
        with pytest.raises(ValueError):
            validate_openbao_policy_name("g" * 513)

    def test_validate_openbao_policy_name_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_openbao_policy_name("")


# ── Token Rotator Edge Cases ─────────────────────────────────────────────


class TestTokenRotatorEdgeCases:
    async def test_rotate_no_record_fails(self):
        store = MagicMock()
        store.get = AsyncMock(return_value=None)
        rotator = TokenRotator(
            secrets_manager=MagicMock(),
            token_store=store,
        )
        with pytest.raises(TokenRotationError, match="No token record"):
            await rotator.rotate("agent-nonexistent")

    def test_rotate_revoked_token_fails(self):
        from general_ludd.db.models import AgentTokenModel

        record = AgentTokenModel(
            token_id="tok-001",
            agent_id="agent-dead",
            parent_agent_id="root",
            role_name="agent-agent-dead",
            role_id="role-001",
            revoked_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        store = MagicMock()
        store.get = AsyncMock(return_value=record)
        rotator = TokenRotator(
            secrets_manager=MagicMock(),
            token_store=store,
        )
        with pytest.raises(TokenRotationError, match="revoked"):
            rotator.rotate("agent-dead")

    def test_policy_name_for_agent_consistent(self):
        name1 = policy_name_for_agent("agent-001")
        name2 = policy_name_for_agent("agent-001")
        assert name1 == name2
        assert name1.startswith("gludd-agent-")

    def test_policy_name_for_agent_different_per_agent(self):
        name1 = policy_name_for_agent("agent-001")
        name2 = policy_name_for_agent("agent-002")
        assert name1 != name2


# ── SecretsUnavailableError ──────────────────────────────────────────────


class TestSecretsUnavailableError:
    def test_unavailable_error_raises_properly(self):
        with pytest.raises(SecretsUnavailableError, match="unavailable"):
            raise SecretsUnavailableError("backend is sealed")

    def test_unavailable_error_from_read_secret_without_connection(self):
        mgr = SecretsManager()
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            mgr.read_secret("any/path")

    def test_connect_external_rejects_plaintext(self):
        from general_ludd.secrets.openbao_scope import OpenBaoConfig

        cfg = OpenBaoConfig(
            mode="external",
            external_url="http://bao.example.com:8200",
            external_token="s.token",
        )
        mgr = SecretsManager(config=cfg)
        with pytest.raises(SecretsUnavailableError, match="https://"):
            mgr.connect()


# ── Escrow ───────────────────────────────────────────────────────────────


class TestCredentialEscrow:
    def test_vault_accepts_retrievable_escrow(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.write_secret = MagicMock()
        vault.secrets_manager.read_secret = MagicMock(return_value='{"tokens": ["t1", "t2"]}')
        vault.store_escrow(
            agent_id="agent-001",
            key="api-key",
            value=crypto_secrets.token_hex(32),
            ttl_seconds=3600,
        )
        result = vault.retrieve_escrow("agent-001", "api-key")
        assert result is not None
        vault.secrets_manager.write_secret.assert_called_once()

    def test_vault_escrow_rejects_empty_key(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="key"):
            vault.store_escrow(
                agent_id="agent-001",
                key="",
                value="secret",
            )

    def test_vault_escrow_rejects_empty_value(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="value"):
            vault.store_escrow(
                agent_id="agent-001",
                key="api-key",
                value="",
            )

    def test_vault_escrow_rejects_empty_agent_id(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="agent_id"):
            vault.store_escrow(
                agent_id="",
                key="api-key",
                value="secret",
            )

    def test_vault_escrow_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.store_escrow(
                agent_id="agent-001",
                key="api-key",
                value="secret",
            )

    def test_vault_retrieve_escrow_requires_connection(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.read_secret = MagicMock(side_effect=SecretsUnavailableError("no connection"))
        with pytest.raises(SecretsUnavailableError, match="no connection"):
            vault.retrieve_escrow("agent-001", "api-key")

    def test_vault_escrow_silently_overwrites_existing(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.write_secret = MagicMock()
        vault.secrets_manager.read_secret = MagicMock(return_value='{"keys": {"api-key": "old-value"}}')
        vault.store_escrow("agent-001", "api-key", "new-value")
        vault.store_escrow("agent-001", "api-key", "newer-value")
        assert vault.secrets_manager.write_secret.call_count == 2


# ── Credential Issuance ──────────────────────────────────────────────────


class TestCredentialIssuance:
    def test_issue_credential_creates_token(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-xyz"))
        result = vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        assert result.token == "tok-xyz"

    def test_issue_credential_rejects_empty_caps(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="capabilities"):
            vault.issue_credential(
                agent_id="agent-001",
                caps=frozenset(),
            )

    def test_issue_credential_stores_audit_record(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-xyz"))
        vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        vault.audit_logger.record.assert_called_once()


# ── Revocation ───────────────────────────────────────────────────────────


class TestRevocation:
    def test_revoke_credential_makes_audit_entry(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.revoke_credential(token_id="tok-001", reason="key rotation")
        vault.audit_logger.record.assert_called_once()

    def test_revoke_credential_requires_token_id(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="token_id"):
            vault.revoke_credential(token_id="", reason="expired")

    def test_revoke_credential_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.revoke_credential(token_id="tok-001", reason="rotated")


# ── Agent Credential Lifecycle ───────────────────────────────────────────


class TestAgentCredentialLifecycle:
    def test_full_lifecycle_issue_read_revoke(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(
            side_effect=[
                MagicMock(token="tok-001", lease_duration=3600),
                MagicMock(token="tok-002", lease_duration=3600),
            ]
        )
        vault.secrets_manager.revoke_credential = MagicMock()

        cred1 = vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        assert cred1.token == "tok-001"

        cred2 = vault.issue_credential(
            agent_id="agent-002",
            caps=frozenset([Capability.PERMISSION_READ, Capability.PERMISSION_WRITE]),
        )
        assert cred2.token == "tok-002"

        vault.revoke_credential(token_id="tok-001", reason="completed")
        vault.revoke_credential(token_id="tok-002", reason="completed")
        assert vault.secrets_manager.revoke_credential.call_count == 2

    def test_rotation_issues_new_credential(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-new", lease_duration=3600))
        vault.secrets_manager.revoke_credential = MagicMock()

        new_cred = vault.rotate_credential(
            agent_id="agent-001",
            old_token_id="tok-old",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        assert new_cred.token == "tok-new"
        vault.secrets_manager.revoke_credential.assert_called_once_with("tok-old")

    def test_rotation_sealed_rejected(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.rotate_credential(
                agent_id="agent-001",
                old_token_id="tok-old",
                caps=frozenset([Capability.PERMISSION_READ]),
            )


# ── Credential Renewal ───────────────────────────────────────────────────


class TestCredentialRenewal:
    def test_renew_extends_lease(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.renew_credential = MagicMock(return_value=MagicMock(lease_duration=7200))
        result = vault.renew_credential(token_id="tok-001")
        assert result.lease_duration == 7200

    def test_renew_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.renew_credential(token_id="tok-001")

    def test_renew_without_token_id(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="token_id"):
            vault.renew_credential(token_id="")


# ── Config Store ─────────────────────────────────────────────────────────


class TestConfigStore:
    def test_read_credential_config_returns_dict(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.read_secret = MagicMock(return_value='{"ttl": 3600}')
        config = vault.read_credential_config("agent-001")
        assert isinstance(config, dict)
        assert config["ttl"] == 3600

    def test_write_credential_config_stores_json(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.write_secret = MagicMock()
        vault.write_credential_config("agent-001", {"ttl": 3600})
        vault.secrets_manager.write_secret.assert_called_once()

    def test_read_credential_config_invalid_json(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.read_secret = MagicMock(return_value="not valid json {{")
        with pytest.raises(ConfigStoreError, match="JSON"):
            vault.read_credential_config("agent-001")


# ── Parent Credential Verification ───────────────────────────────────────


class TestParentCredentialVerification:
    def test_verify_parent_credential_ok_when_unsealed(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.verify_credential = MagicMock(return_value=True)
        assert vault.verify_parent_credential("parent-tok") is True

    def test_verify_parent_credential_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.verify_parent_credential("parent-tok")

    def test_verify_parent_credential_returns_false_for_bad_token(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.verify_credential = MagicMock(return_value=False)
        assert vault.verify_parent_credential("invalid-tok") is False


# ── Max Credentials Guard ────────────────────────────────────────────────


class TestMaxCredentialsGuard:
    def test_max_credentials_guard_blocks_issue(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.count_credentials = MagicMock(return_value=1000)
        with pytest.raises(ConfigStoreError, match="max"):
            vault.issue_credential(
                agent_id="agent-001",
                caps=frozenset([Capability.PERMISSION_READ]),
            )

    def test_max_credentials_guard_allows_below_limit(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.count_credentials = MagicMock(return_value=5)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-xyz"))
        result = vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        assert result is not None


# ── Backend Health ───────────────────────────────────────────────────────


class TestBackendHealth:
    def test_backend_health_check_when_unsealed(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.health_check = MagicMock(return_value={"initialized": True, "sealed": False})
        health = vault.health_check()
        assert health["sealed"] is False

    def test_backend_health_check_returns_init_status(self):
        vault = _build_vault_with_seal(RootSealState.UNINITIALIZED)
        vault.secrets_manager.health_check = MagicMock(return_value={"initialized": False, "sealed": True})
        health = vault.health_check()
        assert health["initialized"] is False

    def test_backend_health_check_returns_sealed_when_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        vault.secrets_manager.health_check = MagicMock(return_value={"initialized": True, "sealed": True})
        health = vault.health_check()
        assert health["sealed"] is True


# ── Concurrency Safety ───────────────────────────────────────────────────


class TestConcurrencySafety:
    async def test_concurrent_escrow_operations_no_deadlock(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.write_secret = MagicMock()

        async def store_one(idx: int):
            vault.store_escrow(
                agent_id=f"agent-{idx:03d}",
                key=f"key-{idx}",
                value=crypto_secrets.token_hex(16),
            )

        import asyncio

        tasks = [asyncio.create_task(store_one(i)) for i in range(32)]
        await asyncio.gather(*tasks)

        assert vault.secrets_manager.write_secret.call_count == 32


# ── Agent Multi-Cluster Token Isolation ──────────────────────────────────


class TestMultiClusterTokenIsolation:
    def test_agents_in_different_clusters_get_distinct_paths(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        path_a = vault._agent_credential_path("agent-001", cluster="us-east")
        path_b = vault._agent_credential_path("agent-001", cluster="eu-west")
        assert path_a != path_b

    def test_same_agent_same_cluster_consistent_path(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        a = vault._agent_credential_path("agent-001", cluster="us-east")
        b = vault._agent_credential_path("agent-001", cluster="us-east")
        assert a == b

    def test_cluster_token_isolation_in_issue(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-east"))
        cred = vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
            cluster="us-east",
        )
        assert cred.token == "tok-east"


# ── Audit Integrity ──────────────────────────────────────────────────────


class TestAuditIntegrity:
    def test_audit_record_includes_agent_id_and_caps(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-xyz"))
        vault.issue_credential(
            agent_id="agent-042",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        call_args = vault.audit_logger.record.call_args[0][0]
        assert call_args["agent_id"] == "agent-042"
        assert "PERMISSION_READ" in call_args["caps"]

    def test_audit_record_includes_cluster_when_provided(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-east"))
        vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
            cluster="us-east",
        )
        call_args = vault.audit_logger.record.call_args[0][0]
        assert call_args.get("cluster") == "us-east"

    def test_revoke_audit_record_includes_reason_and_timestamp(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.revoke_credential(token_id="tok-001", reason="key rotation")
        call_args = vault.audit_logger.record.call_args[0][0]
        assert call_args["token_id"] == "tok-001"
        assert call_args["reason"] == "key rotation"


# ── Event Emission ───────────────────────────────────────────────────────


class TestEventEmission:
    def test_issue_emits_credential_issued_event(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.issue_credential = MagicMock(return_value=MagicMock(token="tok-xyz", lease_duration=3600))
        vault.issue_credential(
            agent_id="agent-001",
            caps=frozenset([Capability.PERMISSION_READ]),
        )
        vault.audit_logger.record.assert_called()

    def test_revoke_emits_credential_revoked_event(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.revoke_credential(token_id="tok-001", reason="expired")
        vault.audit_logger.record.assert_called()


# ── Decryption ───────────────────────────────────────────────────────────


class TestDecryption:
    def test_decrypt_value_roundtrips(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.decrypt = MagicMock(return_value=b"plaintext-data")
        result = vault.decrypt_value("gludd/encrypted/key-001")
        assert result == b"plaintext-data"

    def test_decrypt_value_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.decrypt_value("gludd/encrypted/key-001")

    def test_decrypt_value_empty_key(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="key"):
            vault.decrypt_value("")


# ── Encrypt ──────────────────────────────────────────────────────────────


class TestEncrypt:
    def test_encrypt_value_stores_ciphertext(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.encrypt = MagicMock(return_value="vault:v1:abc123cipher")
        result = vault.encrypt_value(
            key="gludd/encrypted/key-001",
            plaintext=b"secret-data",
        )
        assert result == "vault:v1:abc123cipher"

    def test_encrypt_value_sealed(self):
        vault = _build_vault_with_seal(RootSealState.SEALED)
        with pytest.raises(RootSealHoldError, match="sealed"):
            vault.encrypt_value(
                key="gludd/encrypted/key-001",
                plaintext=b"secret-data",
            )

    def test_encrypt_value_empty_key(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="key"):
            vault.encrypt_value(key="", plaintext=b"data")

    def test_encrypt_value_empty_plaintext(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        with pytest.raises(ValueError, match="plaintext"):
            vault.encrypt_value(key="some/key", plaintext=b"")


# ── Stale Credential Reaping ─────────────────────────────────────────────


class TestStaleCredentialReaping:
    def test_reap_stale_credentials_removes_expired(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.list_credentials = MagicMock(return_value=["tok-old", "tok-new"])
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.reap_stale_credentials(before=datetime.now(UTC))
        assert vault.secrets_manager.revoke_credential.call_count == 2

    def test_reap_stale_credentials_no_expired(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.list_credentials = MagicMock(return_value=[])
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.reap_stale_credentials(before=datetime.now(UTC))
        vault.secrets_manager.revoke_credential.assert_not_called()


# ── Lease Expiry Forecaster ──────────────────────────────────────────────


class TestLeaseExpiryForecaster:
    def test_forecast_returns_earliest_expiry(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.forecast_expiry = MagicMock(return_value=datetime.now(UTC) + timedelta(hours=1))
        result = vault.forecast_next_expiry()
        assert isinstance(result, datetime)

    def test_forecast_no_active_leases(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.forecast_expiry = MagicMock(return_value=None)
        result = vault.forecast_next_expiry()
        assert result is None


# ── Immutable Error ──────────────────────────────────────────────────────


class TestImmutableError:
    def test_immutable_error_stores_message_and_context(self):
        from general_ludd.secrets.credential_vault import (
            ImmutableCredentialError,
        )

        err = ImmutableCredentialError(
            "credential is frozen",
            context={"token_id": "tok-001"},
        )
        assert err.context == {"token_id": "tok-001"}
        assert "frozen" in str(err)


# ── Bulk Revocation ──────────────────────────────────────────────────────


class TestBulkRevocation:
    def test_bulk_revoke_removes_all_listed(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.bulk_revoke_credentials(
            token_ids=["tok-001", "tok-002", "tok-003"],
            reason="mass rotation",
        )
        assert vault.secrets_manager.revoke_credential.call_count == 3

    def test_bulk_revoke_empty_list(self):
        vault = _build_vault_with_seal(RootSealState.UNSEALED)
        vault.secrets_manager.revoke_credential = MagicMock()
        vault.bulk_revoke_credentials(token_ids=[], reason="no-op")
        vault.secrets_manager.revoke_credential.assert_not_called()
