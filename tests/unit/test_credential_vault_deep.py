"""Deep credential vault and secret rotation tests.

Covers: secret rotation lifecycle, credential caching/redaction, TTL
enforcement, access audit pipeline, scope narrowing, and secret versioning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import hvac
import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    AppRoleCreds,
    SecretAlias,
    SecretPermissionDeniedError,
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
from general_ludd.security.permissions import Capability, PermissionSpec
from general_ludd.sts.audit import StsAuditPipeline
from general_ludd.sts.narrowing import (
    CapabilityNarrowing,
    OpenBaoPolicyRenderer,
    PolicyFragment,
)
from general_ludd.sts.rotator import TokenRotationError, TokenRotator

# ── Secret Rotation Lifecycle ────────────────────────────────────────────


class TestSecretRotationLifecycle:
    def test_rotate_approle_secret_id_mints_fresh_and_destroys_old(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client

        resp1 = {"data": {"secret_id": "sid-001", "secret_id_accessor": "acc-a"}}
        resp2 = {"data": {"secret_id": "sid-002", "secret_id_accessor": "acc-b"}}
        mock_client.auth.approle.generate_secret_id.side_effect = [resp1, resp2]

        first = mgr._generate_secret_id("agent-x")
        assert first == "sid-001"
        assert mgr._secret_id_accessors["agent-x"] == ["acc-a"]

        fresh = mgr.rotate_approle_secret_id("agent-x")
        assert fresh == "sid-002"
        mock_client.auth.approle.destroy_secret_id_accessor.assert_called_once_with("agent-x", "acc-a")
        assert mgr._secret_id_accessors["agent-x"] == ["acc-b"]

    def test_rotate_approle_secret_id_requires_connection(self):
        mgr = SecretsManager()
        with pytest.raises(RuntimeError, match="Not connected"):
            mgr.rotate_approle_secret_id("agent-y")

    def test_rotation_destroys_multiple_prior_accessors(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client

        resp1 = {"data": {"secret_id": "sid-a", "secret_id_accessor": "acc-1"}}
        resp2 = {"data": {"secret_id": "sid-b", "secret_id_accessor": "acc-2"}}
        resp3 = {"data": {"secret_id": "sid-c", "secret_id_accessor": "acc-3"}}
        mock_client.auth.approle.generate_secret_id.side_effect = [
            resp1,
            resp2,
            resp3,
        ]

        mgr._generate_secret_id("role-multi")
        mgr._generate_secret_id("role-multi")
        mgr.rotate_approle_secret_id("role-multi")

        assert mock_client.auth.approle.destroy_secret_id_accessor.call_count == 2

    def test_rotation_logs_warning_when_destroy_fails(self, caplog):
        import logging

        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client

        resp1 = {"data": {"secret_id": "sid-1", "secret_id_accessor": "acc-old"}}
        resp2 = {"data": {"secret_id": "sid-2", "secret_id_accessor": "acc-new"}}
        mock_client.auth.approle.generate_secret_id.side_effect = [resp1, resp2]
        mock_client.auth.approle.destroy_secret_id_accessor.side_effect = RuntimeError("backend gone")

        mgr._generate_secret_id("role-flaky")
        with caplog.at_level(logging.WARNING):
            fresh = mgr.rotate_approle_secret_id("role-flaky")

        assert fresh == "sid-2"
        assert "Failed to destroy old secret_id accessor" in caplog.text


# ── Credential Caching & Redaction ───────────────────────────────────────


class TestCredentialCaching:
    def test_track_secret_value_adds_to_known_set(self):
        mgr = SecretsManager()
        assert len(mgr._known_secret_values) == 0
        mgr._track_secret_value("s.a-very-long-secret-token-abc")
        assert "s.a-very-long-secret-token-abc" in mgr._known_secret_values

    def test_track_secret_value_ignores_short_values(self):
        mgr = SecretsManager()
        mgr._track_secret_value("ab")
        mgr._track_secret_value("12345")
        assert len(mgr._known_secret_values) == 0

    def test_track_secret_value_caps_at_max(self):
        mgr = SecretsManager()
        for i in range(mgr._MAX_TRACKED_SECRETS + 20):
            mgr._track_secret_value(f"secret-value-{i:08d}")
        assert len(mgr._known_secret_values) == mgr._MAX_TRACKED_SECRETS

    def test_track_secret_dict_adds_all_values(self):
        mgr = SecretsManager()
        mgr._track_secret_dict(
            {
                "token": "abcdef123456",
                "apikey": "sk-1234567890abc",
            }
        )
        assert "abcdef123456" in mgr._known_secret_values
        assert "sk-1234567890abc" in mgr._known_secret_values

    def test_sanitize_error_exact_match_redaction(self):
        mgr = SecretsManager()
        mgr._track_secret_value("deadbeef12345678")
        exc = RuntimeError("auth failed: deadbeef12345678 was rejected")
        result = mgr._sanitize_error(exc)
        assert "deadbeef12345678" not in result
        assert "REDACTED" in result

    def test_approle_creds_repr_hides_secret_id(self):
        creds = AppRoleCreds(role_id="role-abc", secret_id="secret-xyz")
        assert "role-abc" in repr(creds)
        assert "secret-xyz" not in repr(creds)

    def test_bootstrap_result_repr_hides_tokens(self):
        result = SecretsManager()
        br = result.bootstrap_local()
        r = repr(br)
        assert "initialized=True" in r or "True" in r
        assert br.token not in r

    def test_read_secret_tracks_known_values(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"db_pass": "supersecretdb", "host": "localhost"}},
        }
        mgr.read_secret("db/creds")
        assert "supersecretdb" in mgr._known_secret_values


# ── Secret Permission Denial ─────────────────────────────────────────────


class TestSecretPermissionDenial:
    def test_permission_denied_error_carries_sanitized_patterns(self):
        err = SecretPermissionDeniedError(
            path="prod/keys/signing",
            action="read",
            agent_type="subagent",
            allowed_patterns=["dev/*", "test/*"],
        )
        assert "prod/keys/signing" in str(err)
        assert "subagent" in str(err)
        assert "dev/*" in str(err)
        assert "test/*" in str(err)

    def test_enforce_permission_denied_without_spec(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["dev/*"]},
                )
            ],
        )
        mgr = SecretsManager(config=OpenBaoConfig(), permission_spec=spec)
        assert mgr._permission_spec is spec
        with pytest.raises(SecretPermissionDeniedError, match="secret permission denied"):
            mgr._enforce_permission("prod/key", action="read")

    def test_enforce_permission_noop_with_none_spec(self):
        mgr = SecretsManager(config=OpenBaoConfig(), permission_spec=None)
        result = mgr._enforce_permission("any/path", action="read")
        assert result is None


# ── TTL Enforcement ──────────────────────────────────────────────────────


class TestTTLEnforcement:
    def test_openbao_config_ttl_defaults(self):
        cfg = OpenBaoConfig()
        assert cfg.approle_secret_id_ttl_seconds == 600
        assert cfg.approle_token_ttl_seconds == 3_600
        assert cfg.approle_token_max_ttl_seconds == 3_600
        assert cfg.approle_secret_id_num_uses == 1

    def test_openbao_config_enforces_ttl_ordering(self):
        with pytest.raises(ValueError, match="TTL must not exceed"):
            OpenBaoConfig(
                approle_token_ttl_seconds=7_200,
                approle_token_max_ttl_seconds=3_600,
            )

    def test_ttl_config_lower_bounds(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(approle_secret_id_ttl_seconds=10)

    def test_ttl_config_upper_bounds(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(approle_token_ttl_seconds=90_000)

    def test_openbao_setup_approle_passes_ttl_from_config(self):
        mgr = SecretsManager(
            config=OpenBaoConfig(
                approle_secret_id_ttl_seconds=300,
                approle_secret_id_num_uses=2,
                approle_token_ttl_seconds=1_800,
                approle_token_max_ttl_seconds=3_600,
                approle_token_num_uses=64,
            ),
        )
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.auth.approle.read_role_id.return_value = {
            "data": {"role_id": "role-001"},
        }

        with patch.object(mgr, "_generate_secret_id", return_value="sid-001"):
            mgr.setup_approle("test-role")

        call_kwargs = mock_client.auth.approle.create_role.call_args[1]
        assert call_kwargs["secret_id_ttl"] == 300
        assert call_kwargs["secret_id_num_uses"] == 2
        assert call_kwargs["token_ttl"] == 1_800
        assert call_kwargs["token_num_uses"] == 64

    def test_openbao_ttl_cap_defaults(self):
        cap = OpenBaoTTLCap()
        assert cap.max_ttl_seconds == 900
        assert cap.max_uses == 100

    def test_openbao_ttl_cap_applies_ceiling(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=300, max_uses=10)
        result = cap.apply(requested_ttl_seconds=500, requested_uses=50)
        assert result["ttl_seconds"] == 300
        assert result["uses"] == 10
        assert "capped" in str(result["reason"])

    def test_openbao_ttl_cap_passes_under_limit(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=60, requested_uses=5)
        assert result["ttl_seconds"] == 60
        assert result["uses"] == 5
        assert result["reason"] == "ok"

    def test_openbao_ttl_cap_clamps_negative_to_zero(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=-10, requested_uses=-5)
        assert result["ttl_seconds"] == 0
        assert result["uses"] == 1

    def test_openbao_ttl_cap_constructor_rejects_invalid(self):
        with pytest.raises(ValueError, match="positive"):
            OpenBaoTTLCap(max_ttl_seconds=0)
        with pytest.raises(ValueError, match="positive"):
            OpenBaoTTLCap(max_uses=0)

    def test_token_rotator_needs_rotation_within_window(self):
        rotator = TokenRotator(
            secrets_manager=MagicMock(),
            token_store=MagicMock(),
            rotation_window_seconds=600,
        )
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        assert (
            rotator.needs_rotation(
                expires_at=now + timedelta(seconds=300),
                now=now,
            )
            is True
        )

    def test_token_rotator_needs_rotation_outside_window(self):
        rotator = TokenRotator(
            secrets_manager=MagicMock(),
            token_store=MagicMock(),
            rotation_window_seconds=600,
        )
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        assert (
            rotator.needs_rotation(
                expires_at=now + timedelta(seconds=900),
                now=now,
            )
            is False
        )

    def test_token_rotator_needs_rotation_when_expired(self):
        rotator = TokenRotator(
            secrets_manager=MagicMock(),
            token_store=MagicMock(),
            rotation_window_seconds=600,
        )
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        assert (
            rotator.needs_rotation(
                expires_at=now - timedelta(seconds=1),
                now=now,
            )
            is True
        )

    def test_token_rotator_needs_rotation_none_never(self):
        rotator = TokenRotator(
            secrets_manager=MagicMock(),
            token_store=MagicMock(),
        )
        assert rotator.needs_rotation(expires_at=None) is False


# ── Access Audit Pipeline ────────────────────────────────────────────────


class TestAccessAudit:
    def test_scope_hash_deterministic(self):
        pipeline = StsAuditPipeline(MagicMock())
        h1 = pipeline._scope_hash(["read", "update", "delete"])
        h2 = pipeline._scope_hash(["delete", "read", "update"])
        assert h1 == h2

    def test_scope_hash_none_produces_empty(self):
        pipeline = StsAuditPipeline(MagicMock())
        assert pipeline._scope_hash(None) == ""

    def test_event_dict_shape(self):
        pipeline = StsAuditPipeline(MagicMock())
        event = pipeline._event_dict(
            action="mint",
            agent_id="agent-007",
            parent_agent_id="root",
            scope_hash="abc123",
        )
        assert event["action"] == "mint"
        assert event["agent_id"] == "agent-007"
        assert event["parent_agent_id"] == "root"
        assert event["scope_hash"] == "abc123"
        assert "timestamp" in event

    def test_policy_fragment_frozen(self):
        pf = PolicyFragment(
            path="secret/*",
            capabilities=frozenset({"read", "list"}),
        )
        assert pf.path == "secret/*"
        assert "read" in pf.capabilities
        assert hash(pf) is not None


# ── Scope Narrowing ──────────────────────────────────────────────────────


class TestScopeNarrowing:
    def test_openbao_path_scope_intersection_same_mount_capability(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("dev/*", "staging/*"),
            capabilities=frozenset({"read", "update", "delete"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("dev/*",),
            capabilities=frozenset({"read", "list"}),
        )
        result = parent.intersect(child)
        assert result.capabilities == frozenset({"read"})
        assert result.paths == ("dev/*",)

    def test_openbao_path_scope_intersection_mount_mismatch(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("dev/*",),
            capabilities=frozenset({"read"}),
        )
        child = OpenBaoPathScope(
            mount="kv",
            paths=("dev/*",),
            capabilities=frozenset({"read"}),
        )
        with pytest.raises(OpenBaoScopeDenied, match="mount"):
            parent.intersect(child)

    def test_openbao_path_scope_intersection_no_common_path(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("prod/*",),
            capabilities=frozenset({"read"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("dev/*",),
            capabilities=frozenset({"read"}),
        )
        with pytest.raises(OpenBaoScopeDenied, match="no common path"):
            parent.intersect(child)

    def test_openbao_path_scope_intersection_subtree_widens_parent(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("team/apps/*",),
            capabilities=frozenset({"read", "update"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("team/apps/frontend",),
            capabilities=frozenset({"read"}),
        )
        result = parent.intersect(child)
        assert "team/apps/frontend" in result.paths

    def test_openbao_path_scope_render_policy(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("dev/db",),
            capabilities=frozenset({"read", "list"}),
        )
        hcl = scope.render_policy("test-policy")
        assert "Gludd scoped policy" in hcl
        assert "secret/dev/db" in hcl
        assert "read" in hcl
        assert "list" in hcl

    def test_openbao_scope_request_grant_delegates(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("dev/*",),
            capabilities=frozenset({"read", "update"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("dev/db",),
            capabilities=frozenset({"read"}),
        )
        request = OpenBaoScopeRequest(parent=parent, requested=child)
        granted = request.grant()
        assert granted.capabilities == frozenset({"read"})
        assert "dev/db" in granted.paths

    def test_capability_narrowing_validates_subset(self):
        assert (
            CapabilityNarrowing.validate_narrowing(
                parent_actions={"read", "update", "delete"},
                child_actions={"read", "update"},
            )
            is True
        )

    def test_capability_narrowing_rejects_escalation(self):
        assert (
            CapabilityNarrowing.validate_narrowing(
                parent_actions={"read"},
                child_actions={"read", "delete"},
            )
            is False
        )

    def test_openbao_policy_renderer_empty_actions(self):
        result = OpenBaoPolicyRenderer.render([])
        assert result == ""


# ── Secret Alias Validation ──────────────────────────────────────────────


class TestSecretAliasValidation:
    def test_secret_alias_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            SecretAlias(alias="bad", path="dev/../prod/key")

    def test_secret_alias_null_byte_rejected(self):
        with pytest.raises(ValueError, match="null"):
            SecretAlias(alias="bad", path="dev/key\x00extra")

    def test_secret_alias_empty_path_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            SecretAlias(alias="bad", path="")

    def test_secret_alias_tilde_rejected(self):
        with pytest.raises(ValueError, match="tilde"):
            SecretAlias(alias="bad", path="~/key")

    def test_secret_alias_mount_traversal_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            SecretAlias(alias="bad", path="dev/key", mount="secret/../auth")

    def test_secret_alias_valid(self):
        alias = SecretAlias(alias="db", path="db/credentials", mount="secret")
        assert alias.alias == "db"
        assert alias.path == "db/credentials"
        assert alias.mount == "secret"


# ── OpenBao Path/Mount Validation ────────────────────────────────────────


class TestOpenBaoValidation:
    def test_validate_openbao_mount_rejects_reserved(self):
        with pytest.raises(ValueError):
            validate_openbao_mount("auth")

    def test_validate_openbao_mount_rejects_absolute(self):
        with pytest.raises(ValueError):
            validate_openbao_mount("/secret")

    def test_validate_openbao_mount_accepts_nested(self):
        result = validate_openbao_mount("secret/team-a")
        assert result == "secret/team-a"

    def test_validate_openbao_path_rejects_interior_wildcard(self):
        with pytest.raises(ValueError):
            validate_openbao_path("dev/*/db", allow_terminal_wildcard=True)

    def test_validate_openbao_path_accepts_terminal_wildcard(self):
        result = validate_openbao_path("dev/*", allow_terminal_wildcard=True)
        assert result == "dev/*"

    def test_validate_openbao_policy_name(self):
        result = validate_openbao_policy_name("agent-policy-001")
        assert result == "agent-policy-001"

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

    async def test_rotate_revoked_token_fails(self):
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
            await rotator.rotate("agent-dead")
        assert True

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
        with pytest.raises(SecretsUnavailableError, match="sealed"):
            raise SecretsUnavailableError("backend is sealed")

    def test_unavailable_error_from_read_secret_without_connection(self):
        mgr = SecretsManager()
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            mgr.read_secret("any/path")

    def test_connect_external_rejects_plaintext(self):
        cfg = OpenBaoConfig(
            mode="external",
            external_url="http://bao.example.com:8200",
            external_token="s.token",
        )
        mgr = SecretsManager(config=cfg)
        with pytest.raises(SecretsUnavailableError, match="https://"):
            mgr.connect()


# ── SetupAppRole with Scoped Policy ──────────────────────────────────────


class TestSetupApproleScoped:
    def test_scoped_role_creates_and_deletes_policy_on_failure(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.auth.approle.create_role.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            mgr.setup_approle(
                "scoped-role",
                policy_name="agent-policy-42",
                policy_hcl='path "secret/*" { capabilities = ["read"] }',
            )

        mock_client.sys.delete_policy.assert_called_once_with(
            name="agent-policy-42",
        )

    def test_setup_approle_scoped_suppresses_default_policy(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.auth.approle.read_role_id.return_value = {
            "data": {"role_id": "role-scoped"},
        }

        with patch.object(mgr, "_generate_secret_id", return_value="sid-scoped"):
            mgr.setup_approle(
                "scoped-role",
                policy_name="agent-policy-99",
                policy_hcl='path "secret/dev/*" { capabilities = ["read"] }',
            )

        call_kwargs = mock_client.auth.approle.create_role.call_args[1]
        assert call_kwargs["token_policies"] == ["agent-policy-99"]
        assert call_kwargs["token_no_default_policy"] is True

    def test_setup_approle_rejects_unbalanced_policy_args(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        with pytest.raises(ValueError, match="together"):
            mgr.setup_approle("role-x", policy_name="p", policy_hcl=None)
        with pytest.raises(ValueError, match="together"):
            mgr.setup_approle("role-x", policy_name=None, policy_hcl="some hcl")

    def test_setup_approle_rejects_oversized_hcl(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        with pytest.raises(ValueError, match="65536"):
            mgr.setup_approle(
                "role-x",
                policy_name="p",
                policy_hcl="x" * 70_000,
            )


# ── Secret Versioning (KV v2) ────────────────────────────────────────────


class TestSecretVersioning:
    def test_read_secret_returns_none_when_data_missing(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {},
        }
        result = mgr.read_secret("missing/data/key")
        assert result is None

    def test_delete_secret_deletes_all_versions(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mgr.delete_secret("obsolete/path")
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.assert_called_once_with(
            path="obsolete/path",
            mount_point="secret",
        )

    def test_write_secret_tracks_values_then_writes(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mgr.write_secret("team-a/config", {"key": "abcdefgh12345678"})
        assert "abcdefgh12345678" in mgr._known_secret_values
        mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once()

    def test_list_secrets_returns_empty_on_missing_prefix(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.secrets.kv.v2.list_metadata.side_effect = mgr._client.secrets.kv.v2.list_metadata
        import hvac

        mock_client.secrets.kv.v2.list_metadata.side_effect = hvac.exceptions.InvalidPath(message="no such prefix")
        result = mgr.list_secrets("nonexistent/prefix")
        assert result == []

    def test_list_secrets_returns_keys(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.secrets.kv.v2.list_metadata.return_value = {
            "data": {"keys": ["db", "api", "cache"]},
        }
        result = mgr.list_secrets("team-a")
        assert result == ["db", "api", "cache"]


# ── InvalidPath Handling (genuine not-found) ─────────────────────────────


class TestInvalidPathHandling:
    def test_read_secret_returns_none_on_invalid_path(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        import hvac

        mock_client.secrets.kv.v2.read_secret_version.side_effect = hvac.exceptions.InvalidPath(
            message="no secret at path"
        )
        result = mgr.read_secret("no/such/path")
        assert result is None

    def test_read_secret_reraises_on_non_404_errors(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        mock_client.secrets.kv.v2.read_secret_version.side_effect = hvac.exceptions.Forbidden(message="not authorized")
        with pytest.raises(SecretsUnavailableError, match="unavailable"):
            mgr.read_secret("forbidden/path")


# ── Path Validation ──────────────────────────────────────────────────────


class TestPathValidation:
    def test_write_secret_rejects_traversal(self):
        mgr = SecretsManager()
        with pytest.raises(ValueError, match=r"\.\..*segments are not permitted"):
            mgr.write_secret("dev/../prod/key", {"val": "x"})

    def test_read_secret_rejects_traversal(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        with pytest.raises(ValueError, match=r"\.\..*segments are not permitted"):
            mgr.read_secret("../escape")

    def test_write_secret_rejects_invalid_chars(self):
        mgr = SecretsManager()
        with pytest.raises(ValueError, match="invalid"):
            mgr.write_secret("path;with;semicolons", {"val": "x"})

    def test_list_secrets_rejects_traversal(self):
        mgr = SecretsManager()
        mock_client = MagicMock()
        mgr._client = mock_client
        with pytest.raises(ValueError, match="traversal"):
            mgr.list_secrets("dev/..")


# ── OpenBaoScopeEvidence ─────────────────────────────────────────────────


class TestOpenBaoScopeEvidence:
    def test_evidence_carries_structure(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("dev/db",),
            capabilities=frozenset({"read"}),
        )
        evidence = scope.evidence(
            event_type="scope_granted",
            subject_id="agent-42",
        )
        d = evidence.as_dict()
        assert d["event_type"] == "scope_granted"
        assert d["path_count"] == 1
        assert "read" in d["capabilities"]
        assert d["subject_hash"]
        assert d["scope_hash"]

    def test_evidence_hides_subject_id(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("dev/*",),
            capabilities=frozenset({"read"}),
        )
        evidence = scope.evidence(
            event_type="scope_granted",
            subject_id="sensitive-agent-id-007",
        )
        d = evidence.as_dict()
        assert "sensitive-agent-id-007" not in str(d)
        assert "sensitive-agent-id-007" not in d["subject_hash"]


# ── OpenBaoConfig Validation ─────────────────────────────────────────────


class TestOpenBaoConfigValidation:
    def test_kv_mount_validated_against_openbao_rules(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(kv_mount="/absolute/mount")

    def test_serialized_token_always_redacted(self):
        cfg = OpenBaoConfig(
            mode="external",
            external_url="https://bao.example.com:8200",
            external_token="s.actually-secret-token-value",
        )
        dumped = cfg.model_dump()
        assert dumped["external_token"] != "s.actually-secret-token-value"
        assert "REDACTED" in str(dumped["external_token"])

    def test_mode_must_be_valid(self):
        with pytest.raises(ValueError):
            OpenBaoConfig(mode="bogus")
