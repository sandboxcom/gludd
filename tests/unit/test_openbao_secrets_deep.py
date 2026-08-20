"""Deep OpenBao secrets management tests — edge cases and integration scenarios.

Covers token scope edge cases, mount validation, PSK rotation, permission
enforcement, policy rollback, TTL/lease capping, secret versioning patterns,
and transit-engine path rendering.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.manager import (
    SecretAlias,
    SecretPermissionDeniedError,
    SecretsManager,
    SecretsUnavailableError,
)
from general_ludd.secrets.openbao_scope import (
    _ALLOWED_CAPABILITIES,
    _MAX_MOUNT_CHARS,
    _MAX_PATH_CHARS,
    _MAX_SCOPE_PATHS,
    OpenBaoPathScope,
    OpenBaoScopeDenied,
    OpenBaoScopeRequest,
    OpenBaoTTLCap,
    _intersect_pattern,
    _PathPattern,
    policy_name_for_agent,
    validate_openbao_mount,
    validate_openbao_path,
)
from general_ludd.security.permissions import Capability, PermissionSpec

# ──────────────────────────────────────────────────────────────
#  Token scope validation — edge cases
# ──────────────────────────────────────────────────────────────


class TestTokenScopeEdgeCases:
    """Edge cases for OpenBaoPathScope intersection and validation."""

    def test_wildcard_parent_allows_any_deeper_child(self) -> None:
        parent = OpenBaoPathScope(
            mount="kv",
            paths=("data/*",),
            capabilities={"read"},
        )
        child = OpenBaoPathScope(
            mount="kv",
            paths=("data/a/b/c/d/e",),
            capabilities={"read"},
        )
        ix = parent.intersect(child)
        assert ix.paths == ("data/a/b/c/d/e",)

    def test_wildcard_child_narrowed_to_parent_concrete(self) -> None:
        parent = OpenBaoPathScope(
            mount="kv",
            paths=("data/a/b/c",),
            capabilities={"read"},
        )
        child = OpenBaoPathScope(
            mount="kv",
            paths=("data/a/b/*",),
            capabilities={"read"},
        )
        ix = parent.intersect(child)
        assert ix.paths == ("data/a/b/c",)

    def test_multiple_parent_paths_with_wildcard_overlap(self) -> None:
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/tenants/a/*", "data/tenants/b/*"),
            capabilities={"read", "list"},
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/tenants/a/agents/a1", "data/tenants/b/agents/b1"),
            capabilities={"read"},
        )
        ix = parent.intersect(child)
        assert set(ix.paths) == {"data/tenants/a/agents/a1", "data/tenants/b/agents/b1"}

    def test_overlapping_multi_path_scopes_dedup_intersection(self) -> None:
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/*", "data/foo", "data/foo/bar"),
            capabilities={"read", "update"},
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/foo", "data/foo/bar"),
            capabilities={"read"},
        )
        ix = parent.intersect(child)
        assert ix.paths == ("data/foo", "data/foo/bar")

    def test_exact_path_match_inside_wildcard_parent(self) -> None:
        parent = OpenBaoPathScope(
            mount="kv",
            paths=("data/projects/*",),
            capabilities=frozenset(_ALLOWED_CAPABILITIES),
        )
        child = OpenBaoPathScope(
            mount="kv",
            paths=("data/projects/my-app/secrets/db-password",),
            capabilities={"read", "list"},
        )
        ix = parent.intersect(child)
        assert ix.paths == ("data/projects/my-app/secrets/db-password",)
        assert ix.capabilities == frozenset({"list", "read"})

    def test_scope_intersection_idempotent(self) -> None:
        a = OpenBaoPathScope(mount="s", paths=("d/*",), capabilities={"read", "list"})
        b = OpenBaoPathScope(mount="s", paths=("d/x",), capabilities={"read"})
        once = a.intersect(b)
        twice = a.intersect(b)
        assert once.paths == twice.paths
        assert once.capabilities == twice.capabilities

    def test_max_paths_boundary_accepted(self) -> None:
        paths = tuple(f"data/p{i:03d}" for i in range(_MAX_SCOPE_PATHS))
        scope = OpenBaoPathScope(mount="secret", paths=paths, capabilities={"read"})
        assert len(scope.paths) == _MAX_SCOPE_PATHS

    def test_path_containing_only_hyphens_accepted(self) -> None:
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/x---", "data/foo-bar"),
            capabilities={"read"},
        )
        assert "data/x---" in scope.paths

    def test_path_with_leading_numeric_accepted(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("42data/config",), capabilities={"read"})
        assert scope.paths[0] == "42data/config"


# ──────────────────────────────────────────────────────────────
#  Mount path validation — deep edge cases
# ──────────────────────────────────────────────────────────────


class TestMountValidationDeep:
    """Extended mount validation edge cases."""

    def test_mount_with_multiple_levels_accepted(self) -> None:
        assert validate_openbao_mount("a/b/c/d") == "a/b/c/d"

    def test_mount_exactly_max_length_accepted(self) -> None:
        validate_openbao_mount("a" * _MAX_MOUNT_CHARS)

    def test_mount_with_unicode_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secr\u00e9t")

    def test_mount_with_spaces_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("  secret")

    def test_mount_with_consecutive_slashes_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret//extra")

    def test_mount_segment_starting_with_period_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret/.hidden")

    def test_mount_segment_single_period_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret/./extra")

    def test_identity_mount_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("identity")

    def test_cubbyhole_mount_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("cubbyhole")


class TestPathValidationDeep:
    """Extended path validation edge cases."""

    def test_path_with_colons_accepted(self) -> None:
        assert validate_openbao_path("data/key:value", allow_terminal_wildcard=True) == "data/key:value"

    def test_path_with_underscores_accepted(self) -> None:
        assert validate_openbao_path("data/my_key/sub_path", allow_terminal_wildcard=True) == "data/my_key/sub_path"

    def test_path_exactly_max_length_accepted(self) -> None:
        validate_openbao_path("a" * _MAX_PATH_CHARS, allow_terminal_wildcard=True)

    def test_path_with_unicode_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/\u00e9", allow_terminal_wildcard=True)

    def test_path_with_tab_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/\tfoo", allow_terminal_wildcard=True)

    def test_path_segment_with_double_period_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/..", allow_terminal_wildcard=True)

    def test_path_with_trailing_wildcard_and_slash_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/*/", allow_terminal_wildcard=True)


# ──────────────────────────────────────────────────────────────
#  PSK rotation scenarios
# ──────────────────────────────────────────────────────────────


class TestPskRotationDeep:
    """PSK / AppRole secret_id rotation deep scenarios."""

    def test_rotation_destroys_all_prior_accessors(self) -> None:
        client = MagicMock()
        client.auth.approle.read_role_id.return_value = {"data": {"role_id": "r-1"}}
        accessors: list[str] = []

        def generate_side_effect(role_name: str) -> dict:
            nonlocal accessors
            acc = f"accessor-{len(accessors) + 1}"
            accessors.append(acc)
            return {"data": {"secret_id": f"s-{len(accessors)}", "secret_id_accessor": acc}}

        client.auth.approle.generate_secret_id.side_effect = generate_side_effect
        manager = SecretsManager(client=client)

        manager.setup_approle("agent-rot")
        manager.rotate_approle_secret_id("agent-rot")
        manager.rotate_approle_secret_id("agent-rot")

        destroy_calls = client.auth.approle.destroy_secret_id_accessor.call_args_list
        destroyed_accessors = [call.args[1] for call in destroy_calls]
        assert accessors[0] in destroyed_accessors
        assert accessors[1] in destroyed_accessors
        assert accessors[2] not in destroyed_accessors

    def test_rotation_returns_new_secret_id_that_differs(self) -> None:
        client = MagicMock()
        client.auth.approle.read_role_id.return_value = {"data": {"role_id": "r-1"}}
        sid_counter = {"n": 0}

        def generate_side_effect(role_name: str) -> dict:
            sid_counter["n"] += 1
            return {"data": {"secret_id": f"s-new-{sid_counter['n']}", "secret_id_accessor": f"acc-{sid_counter['n']}"}}

        client.auth.approle.generate_secret_id.side_effect = generate_side_effect
        manager = SecretsManager(client=client)
        manager.setup_approle("agent-rot2")
        new_sid = manager.rotate_approle_secret_id("agent-rot2")
        assert new_sid == "s-new-2"

    def test_rotation_handles_destroy_failure_gracefully(self) -> None:
        client = MagicMock()
        client.auth.approle.read_role_id.return_value = {"data": {"role_id": "r-1"}}
        client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "s-1", "secret_id_accessor": "acc-1"}
        }
        client.auth.approle.generate_secret_id.side_effect = [
            {"data": {"secret_id": "s-1", "secret_id_accessor": "acc-1"}},
            {"data": {"secret_id": "s-2", "secret_id_accessor": "acc-2"}},
        ]
        client.auth.approle.destroy_secret_id_accessor.side_effect = RuntimeError("backend hiccup")

        manager = SecretsManager(client=client)
        manager.setup_approle("agent-hiccup")
        new_sid = manager.rotate_approle_secret_id("agent-hiccup")
        assert new_sid == "s-2"

    def test_rotation_with_zero_prior_accessors_noops_destroy(self) -> None:
        client = MagicMock()
        client.auth.approle.read_role_id.return_value = {"data": {"role_id": "r-1"}}
        client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "s-fresh", "secret_id_accessor": None}
        }

        manager = SecretsManager(client=client)
        manager.setup_approle("agent-noacc")
        client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "s-new", "secret_id_accessor": "acc-new"}
        }
        new_sid = manager.rotate_approle_secret_id("agent-noacc")
        assert new_sid == "s-new"
        client.auth.approle.destroy_secret_id_accessor.assert_not_called()


# ──────────────────────────────────────────────────────────────
#  Secret versioning and rollback
# ──────────────────────────────────────────────────────────────


class TestSecretVersioningDeep:
    """Secret lifecycle: write, read version, delete with rollback semantics."""

    def test_setup_approle_rolls_back_policy_on_role_creation_failure(self) -> None:
        client = MagicMock()
        client.auth.approle.create_role.side_effect = RuntimeError("backend down")
        manager = SecretsManager(client=client)

        with pytest.raises(RuntimeError, match="backend down"):
            manager.setup_approle(
                "agent-boom",
                policy_name="gludd-agent-test",
                policy_hcl='path "secret/data/*" { capabilities = ["read"] }',
            )
        client.sys.delete_policy.assert_called_once_with(name="gludd-agent-test")

    def test_setup_approle_swallows_cleanup_failure_during_rollback(self) -> None:
        client = MagicMock()
        client.auth.approle.create_role.side_effect = RuntimeError("backend down")
        client.sys.delete_policy.side_effect = RuntimeError("cleanup also failed")
        manager = SecretsManager(client=client)

        with pytest.raises(RuntimeError, match="backend down"):
            manager.setup_approle(
                "agent-doublefail",
                policy_name="gludd-agent-df",
                policy_hcl='path "secret/data/*" {}',
            )
        client.sys.delete_policy.assert_called_once_with(name="gludd-agent-df")

    def test_setup_approle_rejects_policy_name_without_policy_body(self) -> None:
        client = MagicMock()
        manager = SecretsManager(client=client)

        with pytest.raises(ValueError, match="together"):
            manager.setup_approle("agent-half", policy_name="gludd-agent-half")

        with pytest.raises(ValueError, match="together"):
            manager.setup_approle("agent-half2", policy_hcl='path "s/*" {}')

    def test_setup_approle_rejects_null_byte_in_policy_hcl(self) -> None:
        client = MagicMock()
        manager = SecretsManager(client=client)

        with pytest.raises(ValueError, match="OpenBao policy HCL"):
            manager.setup_approle(
                "agent-null",
                policy_name="gludd-agent-null",
                policy_hcl='path "secret/data/*"\x00 {}',
            )

    def test_setup_approle_rejects_empty_policy_hcl(self) -> None:
        client = MagicMock()
        manager = SecretsManager(client=client)

        with pytest.raises(ValueError):
            manager.setup_approle(
                "agent-empty",
                policy_name="gludd-agent-empty",
                policy_hcl="",
            )

    def test_setup_approle_rejects_oversized_policy_hcl(self) -> None:
        client = MagicMock()
        manager = SecretsManager(client=client)

        with pytest.raises(ValueError):
            manager.setup_approle(
                "agent-big",
                policy_name="gludd-agent-big",
                policy_hcl="x" * 70_000,
            )

    def test_setup_approle_sets_token_no_default_policy_when_scoped(self) -> None:
        client = MagicMock()
        client.auth.approle.read_role_id.return_value = {"data": {"role_id": "r-scoped"}}
        client.auth.approle.generate_secret_id.return_value = {
            "data": {"secret_id": "s-scoped", "secret_id_accessor": "acc-scoped"}
        }
        manager = SecretsManager(client=client)
        manager.setup_approle(
            "agent-scoped",
            policy_name="gludd-agent-scoped",
            policy_hcl='path "secret/data/tenants/acme/*" { capabilities = ["read"] }',
        )
        role_kwargs = client.auth.approle.create_role.call_args.kwargs
        assert role_kwargs["token_no_default_policy"] is True
        assert role_kwargs["token_policies"] == ["gludd-agent-scoped"]

    def test_write_then_read_secret_roundtrip(self) -> None:
        client = MagicMock()
        manager = SecretsManager(client=client)
        manager._client = client
        manager.write_secret("my/key", {"value": "hello"})
        client.secrets.kv.v2.create_or_update_secret.assert_called_once_with(
            path="my/key",
            secret={"value": "hello"},
            mount_point="secret",
        )

    def test_read_secret_returns_none_on_genuine_not_found(self) -> None:
        import hvac

        client = MagicMock()
        client.secrets.kv.v2.read_secret_version.side_effect = hvac.exceptions.InvalidPath("404")
        manager = SecretsManager(client=client)
        manager._client = client
        result = manager.read_secret("nonexistent/key")
        assert result is None

    def test_read_secret_raises_on_backend_outage(self) -> None:
        import hvac

        client = MagicMock()
        client.secrets.kv.v2.read_secret_version.side_effect = hvac.exceptions.Forbidden("sealed")
        manager = SecretsManager(client=client)
        manager._client = client
        with pytest.raises(SecretsUnavailableError):
            manager.read_secret("any/key")

    def test_read_secret_without_client_raises_secrets_unavailable(self) -> None:
        manager = SecretsManager()
        with pytest.raises(SecretsUnavailableError, match="not connected"):
            manager.read_secret("any/key")

    def test_write_secret_rejects_null_byte(self) -> None:
        manager = SecretsManager()
        with pytest.raises(ValueError):
            manager.write_secret("path\x00break", {"v": "x"})


# ──────────────────────────────────────────────────────────────
#  Transit encryption / decryption — path scoping
# ──────────────────────────────────────────────────────────────


class TestTransitPathScoping:
    """Transit engine and cross-mount path validation."""

    def test_all_six_capabilities_accepted_in_scope(self) -> None:
        scope = OpenBaoPathScope(
            mount="transit",
            paths=("encrypt/gludd-key", "decrypt/gludd-key"),
            capabilities={"create", "delete", "list", "patch", "read", "update"},
        )
        assert len(scope.capabilities) == 6

    def test_transit_mount_paths_pass_validation(self) -> None:
        assert validate_openbao_mount("transit") == "transit"

    def test_sys_mount_capabilities_blocked_without_allow_reserved(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("sys")
        assert validate_openbao_mount("sys", allow_reserved=True) == "sys"

    def test_policy_rendering_preserves_all_capabilities_order(self) -> None:
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/config",),
            capabilities={"update", "create", "read"},
        )
        hcl = scope.render_policy("gludd-agent-caporder")
        assert '"create"' in hcl
        assert '"read"' in hcl
        assert '"update"' in hcl

    def test_cross_mount_path_denied_at_intersection(self) -> None:
        parent = OpenBaoPathScope(mount="kv", paths=("data/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="transit", paths=("encrypt/key",), capabilities={"read"})
        with pytest.raises(OpenBaoScopeDenied, match="mount"):
            parent.intersect(child)

    def test_transit_engine_path_rendering(self) -> None:
        scope = OpenBaoPathScope(
            mount="transit",
            paths=("encrypt/my-key", "decrypt/my-key"),
            capabilities={"update"},
        )
        hcl = scope.render_policy("gludd-agent-transit")
        assert 'path "transit/encrypt/my-key"' in hcl
        assert 'path "transit/decrypt/my-key"' in hcl


# ──────────────────────────────────────────────────────────────
#  Lease renewal and expiry (TTL capping deep)
# ──────────────────────────────────────────────────────────────


class TestLeaseTTLDeep:
    """Lease, TTL, and expiry edge cases beyond basic capping."""

    def test_config_token_ttl_cannot_exceed_max_ttl(self) -> None:
        with pytest.raises(ValidationError, match="token TTL"):
            OpenBaoConfig(
                approle_token_ttl_seconds=7200,
                approle_token_max_ttl_seconds=3600,
            )

    def test_config_token_ttl_at_max_ttl_accepted(self) -> None:
        cfg = OpenBaoConfig(
            approle_token_ttl_seconds=3600,
            approle_token_max_ttl_seconds=3600,
        )
        assert cfg.approle_token_ttl_seconds == 3600
        assert cfg.approle_token_max_ttl_seconds == 3600

    def test_config_secret_id_ttl_at_minimum_accepted(self) -> None:
        cfg = OpenBaoConfig(approle_secret_id_ttl_seconds=30)
        assert cfg.approle_secret_id_ttl_seconds == 30

    def test_config_secret_id_ttl_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OpenBaoConfig(approle_secret_id_ttl_seconds=29)

    def test_config_token_num_uses_at_minimum_accepted(self) -> None:
        cfg = OpenBaoConfig(approle_token_num_uses=1)
        assert cfg.approle_token_num_uses == 1

    def test_config_token_num_uses_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OpenBaoConfig(approle_token_num_uses=0)

    def test_config_token_num_uses_above_maximum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OpenBaoConfig(approle_token_num_uses=100_001)

    def test_config_secret_id_num_uses_at_one_accepted(self) -> None:
        cfg = OpenBaoConfig(approle_secret_id_num_uses=1)
        assert cfg.approle_secret_id_num_uses == 1

    def test_config_secret_id_num_uses_below_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OpenBaoConfig(approle_secret_id_num_uses=0)

    def test_ttl_cap_validates_constructor(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            OpenBaoTTLCap(max_ttl_seconds=0)

        with pytest.raises(ValueError, match="positive"):
            OpenBaoTTLCap(max_uses=0)

    def test_ttl_cap_zero_request_clamped_to_min(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=600, max_uses=50)
        result = cap.apply(requested_ttl_seconds=0, requested_uses=0)
        assert result["ttl_seconds"] == 0
        assert result["uses"] == 1

    def test_ttl_cap_float_ttl_truncated(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=600)
        result = cap.apply(requested_ttl_seconds=299.9, requested_uses=5)
        assert result["ttl_seconds"] == 299
        assert result["reason"] == "ok"

    def test_ttl_cap_both_exceeded_reason(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=300, max_uses=10)
        result = cap.apply(requested_ttl_seconds=1000, requested_uses=5000)
        assert result["ttl_seconds"] == 300
        assert result["uses"] == 10
        assert result["reason"] == "capped: ttl+uses"

    def test_ttl_cap_ttl_only_exceeded_reason(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=300, max_uses=1000)
        result = cap.apply(requested_ttl_seconds=500, requested_uses=5)
        assert result["ttl_seconds"] == 300
        assert result["uses"] == 5
        assert result["reason"] == "capped: ttl"

    def test_ttl_cap_uses_only_exceeded_reason(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=3000, max_uses=10)
        result = cap.apply(requested_ttl_seconds=30, requested_uses=500)
        assert result["ttl_seconds"] == 30
        assert result["uses"] == 10
        assert result["reason"] == "capped: uses"

    def test_ttl_cap_exact_boundary_not_capped(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=600, max_uses=50)
        result = cap.apply(requested_ttl_seconds=600, requested_uses=50)
        assert result["ttl_seconds"] == 600
        assert result["uses"] == 50
        assert result["reason"] == "ok"

    def test_ttl_cap_exceeds_by_one_is_capped(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=600, max_uses=50)
        result = cap.apply(requested_ttl_seconds=601, requested_uses=51)
        assert result["ttl_seconds"] == 600
        assert result["uses"] == 50
        assert result["reason"] == "capped: ttl+uses"

    def test_ttl_cap_non_default_constructor_values(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=42, max_uses=7)
        assert cap.max_ttl_seconds == 42
        assert cap.max_uses == 7
        result = cap.apply(requested_ttl_seconds=100, requested_uses=100)
        assert result["ttl_seconds"] == 42
        assert result["uses"] == 7


# ──────────────────────────────────────────────────────────────
#  Policy attachment and enforcement
# ──────────────────────────────────────────────────────────────


class TestPolicyEnforcementDeep:
    """PermissionSpec integration with SecretsManager enforcement."""

    @staticmethod
    def _make_spec(
        resource: str = "secret:openbao",
        actions: list[str] | None = None,
        constraints: dict | None = None,
        denied: list[Capability] | None = None,
    ) -> PermissionSpec:
        return PermissionSpec(
            agent_type="test-agent",
            capabilities=[
                Capability(
                    resource=resource,
                    actions=actions or ["read"],
                    constraints=constraints or {"openbao_paths": ["secret/data/gludd/*"]},
                ),
            ],
            denied=denied or [],
        )

    def test_permission_denial_carries_agent_type(self) -> None:
        spec = self._make_spec(actions=["write"], constraints={"openbao_paths": ["secret/data/gludd/*"]})
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError) as exc_info:
            manager._enforce_permission("secret/data/gludd/build/config", "read")
        assert exc_info.value.agent_type == "test-agent"

    def test_denied_entry_blocks_even_when_grant_exists(self) -> None:
        spec = PermissionSpec(
            agent_type="agent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/blocked-path"]},
                ),
            ],
        )
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError):
            manager._enforce_permission("secret/data/blocked-path", "read")

    def test_denied_entry_with_empty_actions_blocks_all(self) -> None:
        spec = PermissionSpec(
            agent_type="agent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read", "write", "list", "delete"],
                    constraints={"openbao_paths": ["secret/data/*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=[],
                    constraints={"openbao_paths": ["secret/data/forbidden/*"]},
                ),
            ],
        )
        manager = SecretsManager(permission_spec=spec)
        for action in ("read", "write", "list"):
            with pytest.raises(SecretPermissionDeniedError):
                manager._enforce_permission("secret/data/forbidden/x", action)

    def test_allowed_paths_in_denial_error_are_sanitized(self) -> None:
        spec = self._make_spec(
            actions=["read"],
            constraints={"openbao_paths": ["secret/data/gludd/*"]},
        )
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError) as exc_info:
            manager._enforce_permission("secret/data/other/x", "read")
        assert "secret/data/gludd/*" in str(exc_info.value)
        assert "secret/data/other/x" in str(exc_info.value)

    def test_missing_capability_raises_with_empty_patterns(self) -> None:
        spec = PermissionSpec(agent_type="agent", capabilities=[])
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError) as exc_info:
            manager._enforce_permission("secret/data/x", "read")
        assert exc_info.value.allowed_patterns == []

    def test_path_not_matching_any_glob_denied(self) -> None:
        spec = self._make_spec(
            actions=["read"],
            constraints={"openbao_paths": ["secret/data/gludd/build/*"]},
        )
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError):
            manager._enforce_permission("secret/data/gludd/other/nested/key", "read")

    def test_glob_matches_deep_paths(self) -> None:
        spec = self._make_spec(
            actions=["read"],
            constraints={"openbao_paths": ["secret/data/gludd/*"]},
        )
        manager = SecretsManager(permission_spec=spec)
        manager._enforce_permission("secret/data/gludd/a/b/c/d/e", "read")

    def test_null_spec_bypasses_enforcement(self) -> None:
        manager = SecretsManager(permission_spec=None)
        manager._enforce_permission("any/path", "read")

    def test_action_not_in_capability_denied(self) -> None:
        spec = self._make_spec(actions=["read"])
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError):
            manager._enforce_permission("secret/data/gludd/config", "write")

    def test_secret_permission_denied_error_inherits_secrets_unavailable(self) -> None:
        assert issubclass(SecretPermissionDeniedError, SecretsUnavailableError)

    def test_denied_with_path_prefix_constraint(self) -> None:
        spec = PermissionSpec(
            agent_type="agent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"path_prefix": "secret/data/sensitive"},
                ),
            ],
        )
        manager = SecretsManager(permission_spec=spec)
        with pytest.raises(SecretPermissionDeniedError):
            manager._enforce_permission("secret/data/sensitive/key", "read")

    def test_denied_with_path_prefix_allows_unrelated(self) -> None:
        spec = PermissionSpec(
            agent_type="agent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"path_prefix": "secret/data/sensitive"},
                ),
            ],
        )
        manager = SecretsManager(permission_spec=spec)
        manager._enforce_permission("secret/data/ok/path", "read")

    def test_denied_path_prefix_does_not_match_sibling_name_collision(self) -> None:
        spec = PermissionSpec(
            agent_type="agent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/*"]},
                ),
            ],
            denied=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"path_prefix": "secret/data/sensitive"},
                ),
            ],
        )
        manager = SecretsManager(permission_spec=spec)

        manager._enforce_permission("secret/data/sensitive-backup/key", "read")


# ──────────────────────────────────────────────────────────────
#  Policy name and evidence — deep recurrence
# ──────────────────────────────────────────────────────────────


class TestPolicyNameDeep:
    """Deeper policy name generation and evidence coverage."""

    def test_policy_name_same_agent_id_same_hash(self) -> None:
        a = policy_name_for_agent("agent-42")
        b = policy_name_for_agent("agent-42")
        assert a == b

    def test_policy_name_different_agents_different(self) -> None:
        a = policy_name_for_agent("agent-1")
        b = policy_name_for_agent("agent-2")
        assert a != b

    def test_policy_name_prefix_always_gludd_agent(self) -> None:
        for agent_id in ("a", "agent-x", "very-very-very-long-id-" + "x" * 200):
            name = policy_name_for_agent(agent_id)
            assert name.startswith("gludd-agent-"), f"unexpected name for {agent_id!r}: {name!r}"
            assert len(name) <= 128

    def test_evidence_carries_all_fields(self) -> None:
        scope = OpenBaoPathScope(mount="kv", paths=("x",), capabilities={"read"})
        ev = scope.evidence(event_type="scope_granted", subject_id="agent-1")
        assert ev.event_type == "scope_granted"
        assert len(ev.subject_hash) == 32
        assert len(ev.scope_hash) == 32
        assert ev.path_count == 1
        assert ev.capabilities == ("read",)
        assert ev.reason_code == "ok"

    def test_evidence_denied_with_custom_reason(self) -> None:
        scope = OpenBaoPathScope(mount="kv", paths=("x",), capabilities={"read"})
        ev = scope.evidence(
            event_type="scope_denied",
            subject_id="agent-evil",
            reason_code="no_common_path",
        )
        assert ev.event_type == "scope_denied"
        assert ev.reason_code == "no_common_path"

    def test_evidence_revoked_event_type(self) -> None:
        scope = OpenBaoPathScope(mount="kv", paths=("x",), capabilities={"read"})
        ev = scope.evidence(event_type="scope_revoked", subject_id="agent-gone")
        assert ev.event_type == "scope_revoked"

    def test_evidence_as_dict_roundtrip(self) -> None:
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/a", "data/b"),
            capabilities={"read", "list"},
        )
        ev = scope.evidence(event_type="scope_granted", subject_id="s")
        d = ev.as_dict()
        assert d["event_type"] == "scope_granted"
        assert d["path_count"] == 2
        assert set(d["capabilities"]) == {"list", "read"}


# ──────────────────────────────────────────────────────────────
#  _PathPattern and _intersect_pattern — internal logic
# ──────────────────────────────────────────────────────────────


class TestPathPatternInternals:
    """Tests for internal _PathPattern and _intersect_pattern logic."""

    def test_parse_simple_path(self) -> None:
        pp = _PathPattern.parse("a/b/c")
        assert pp.segments == ("a", "b", "c")
        assert pp.subtree is False
        assert pp.render() == "a/b/c"

    def test_parse_wildcard_path(self) -> None:
        pp = _PathPattern.parse("a/b/*")
        assert pp.segments == ("a", "b")
        assert pp.subtree is True
        assert pp.render() == "a/b/*"

    def test_intersect_identical_patterns(self) -> None:
        a = _PathPattern.parse("x/y")
        b = _PathPattern.parse("x/y")
        result = _intersect_pattern(a, b)
        assert result is not None
        assert result.render() == "x/y"

    def test_intersect_both_wildcard(self) -> None:
        a = _PathPattern.parse("x/*")
        b = _PathPattern.parse("x/*")
        result = _intersect_pattern(a, b)
        assert result is not None
        assert result.render() == "x/*"

    def test_intersect_parent_wildcard_child_concrete(self) -> None:
        a = _PathPattern.parse("x/*")
        b = _PathPattern.parse("x/y")
        result = _intersect_pattern(a, b)
        assert result is not None
        assert result.render() == "x/y"

    def test_intersect_child_wildcard_parent_concrete(self) -> None:
        a = _PathPattern.parse("x/y")
        b = _PathPattern.parse("x/*")
        result = _intersect_pattern(a, b)
        assert result is not None
        assert result.render() == "x/y"

    def test_intersect_disjoint_prefixes(self) -> None:
        a = _PathPattern.parse("a/*")
        b = _PathPattern.parse("b/x")
        result = _intersect_pattern(a, b)
        assert result is None

    def test_intersect_child_shorter_than_parent_wildcard(self) -> None:
        a = _PathPattern.parse("a/b/*")
        b = _PathPattern.parse("a/*")
        result = _intersect_pattern(a, b)
        assert result is not None
        assert result.render() == "a/b/*"

    def test_intersect_child_longer_but_no_wildcard(self) -> None:
        a = _PathPattern.parse("a/*")
        b = _PathPattern.parse("a/b/c")
        result = _intersect_pattern(a, b)
        assert result is not None
        assert result.render() == "a/b/c"

    def test_intersect_concrete_with_longer_concrete(self) -> None:
        a = _PathPattern.parse("a/b")
        b = _PathPattern.parse("a/b/c")
        result = _intersect_pattern(a, b)
        assert result is None


# ──────────────────────────────────────────────────────────────
#  SecretAlias and mount restriction
# ──────────────────────────────────────────────────────────────


class TestSecretAliasDeep:
    """SecretAlias validation and SecretsManager alias registration."""

    def test_alias_registration_accepted(self) -> None:
        manager = SecretsManager()
        alias = SecretAlias("db-pass", "data/db/password", mount="secret")
        manager.register_alias(alias)
        assert "db-pass" in manager.list_aliases()

    def test_alias_unpermitted_mount_rejected(self) -> None:
        manager = SecretsManager()
        with pytest.raises(ValueError, match="mount"):
            manager.register_alias(SecretAlias("x", "data/foo", mount="unlisted"))

    def test_alias_path_with_tilde_rejected(self) -> None:
        with pytest.raises(ValueError):
            SecretAlias("x", "~/data", mount="secret")

    def test_alias_path_empty_rejected(self) -> None:
        with pytest.raises(ValueError):
            SecretAlias("x", "", mount="secret")


# ──────────────────────────────────────────────────────────────
#  Policy rendering — deep output shape
# ──────────────────────────────────────────────────────────────


class TestPolicyRenderingDeep:
    """Extended policy rendering edge cases."""

    def test_single_path_single_capability_output(self) -> None:
        scope = OpenBaoPathScope(mount="kv", paths=("data/db",), capabilities={"read"})
        hcl = scope.render_policy("gludd-agent-r")
        assert 'path "kv/data/db"' in hcl
        assert 'capabilities = ["read"]' in hcl
        assert hcl.endswith("\n")

    def test_multi_path_multi_capability_output(self) -> None:
        scope = OpenBaoPathScope(
            mount="kv",
            paths=("data/a", "data/b", "data/c"),
            capabilities={"read", "list", "create"},
        )
        hcl = scope.render_policy("gludd-agent-multi")
        assert hcl.count('path "kv/') == 3
        assert 'capabilities = ["create", "list", "read"]' in hcl

    def test_policy_name_appears_in_header(self) -> None:
        scope = OpenBaoPathScope(mount="s", paths=("x",), capabilities={"read"})
        hcl = scope.render_policy("gludd-agent-hdr")
        assert 'Gludd scoped policy "gludd-agent-hdr"' in hcl

    def test_invalid_policy_name_rejected(self) -> None:
        scope = OpenBaoPathScope(mount="s", paths=("x",), capabilities={"read"})
        with pytest.raises(ValueError):
            scope.render_policy("bad/name!@#")

    def test_empty_policy_name_rejected(self) -> None:
        scope = OpenBaoPathScope(mount="s", paths=("x",), capabilities={"read"})
        with pytest.raises(ValueError):
            scope.render_policy("")


# ──────────────────────────────────────────────────────────────
#  Scope request edge cases
# ──────────────────────────────────────────────────────────────


class TestScopeRequestDeep:
    """OpenBaoScopeRequest edge cases."""

    def test_grant_propagates_denial(self) -> None:
        parent = OpenBaoPathScope(mount="kv", paths=("data/a/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="kv", paths=("data/b",), capabilities={"read"})
        request = OpenBaoScopeRequest(parent=parent, requested=child)
        with pytest.raises(OpenBaoScopeDenied, match="no common path"):
            request.grant()

    def test_grant_narrows_capabilities(self) -> None:
        parent = OpenBaoPathScope(mount="kv", paths=("data/*",), capabilities={"read", "list", "update"})
        child = OpenBaoPathScope(mount="kv", paths=("data/x",), capabilities={"read"})
        request = OpenBaoScopeRequest(parent=parent, requested=child)
        granted = request.grant()
        assert granted.capabilities == frozenset({"read"})
