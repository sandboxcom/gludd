"""Deep tests for secrets config: path resolution, mount config, policy
generation, scope templates, PSK handling.

Covers:
- SecretAlias / _validate_secret_path / project scoping (path resolution)
- validate_openbao_mount / _PERMITTED_MOUNTS / register_alias (mount config)
- OpenBaoPathScope.render_policy / policy_name_for_agent (policy generation)
- OpenBaoPathScope.intersect / _PathPattern parsing (scope template)
- EnvSecretsManager allowlist — PSK blocked, API keys allowed (PSK handling)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from general_ludd.secrets.config import OpenBaoConfig
from general_ludd.secrets.cosign import _scoped_path as cosign_scoped_path
from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.gitsign import _scoped_path as gitsign_scoped_path
from general_ludd.secrets.manager import SecretAlias, SecretsManager
from general_ludd.secrets.openbao_scope import (
    OpenBaoPathScope,
    OpenBaoScopeDenied,
    OpenBaoScopeRequest,
    OpenBaoTTLCap,
    _PathPattern,
    policy_name_for_agent,
    validate_openbao_mount,
    validate_openbao_policy_name,
)
from general_ludd.secrets.project_secrets import ProjectSecretsManager

# ── Path Resolution ──


class TestSecretAliasPathValidation:
    def test_valid_path_accepted(self):
        alias = SecretAlias(alias="foo", path="db/password", mount="secret")
        assert alias.path == "db/password"
        assert alias.mount == "secret"

    def test_valid_path_with_special_chars(self):
        alias = SecretAlias(alias="a", path="model-profiles/abc123/credential_alias")
        assert "abc123" in alias.path

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            SecretAlias(alias="a", path="")

    def test_null_byte_in_path_rejected(self):
        with pytest.raises(ValueError, match="null byte"):
            SecretAlias(alias="a", path="foo\x00bar")

    def test_dot_dot_traversal_rejected(self):
        with pytest.raises(ValueError, match="traversal segment"):
            SecretAlias(alias="a", path="secret/../other")

    def test_tilde_rejected(self):
        with pytest.raises(ValueError, match="tilde"):
            SecretAlias(alias="a", path="~/.ssh/id_rsa")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias(alias="a", path="foo\\bar")

    def test_semicolon_rejected(self):
        with pytest.raises(ValueError, match="invalid characters"):
            SecretAlias(alias="a", path="foo;rm")


class TestValidateSecretPath:
    def test_valid(self):
        assert SecretsManager._validate_secret_path("db/password") is None

    def test_dot_dot_traversal_rejected(self):
        with pytest.raises(ValueError, match="segments are not permitted"):
            SecretsManager._validate_secret_path("secret/../root")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="must match"):
            SecretsManager._validate_secret_path("foo\\bar")


class TestProjectSecretsScoping:
    def test_scoped_path_containment(self):
        base = SecretsManager()
        psm = ProjectSecretsManager(base, project_id="project-alpha")
        result = psm._scoped_path("db/password")
        assert result == "projects/project-alpha/db/password"

    def test_scoped_path_simple_key(self):
        base = SecretsManager()
        psm = ProjectSecretsManager(base, project_id="myproj")
        result = psm._scoped_path("api_key")
        assert result == "projects/myproj/api_key"

    def test_traversal_blocked(self):
        base = SecretsManager()
        psm = ProjectSecretsManager(base, project_id="myproj")
        with pytest.raises(ValueError, match="escapes project scope"):
            psm._scoped_path("../../other/secrets")

    def test_invalid_project_id_slash(self):
        with pytest.raises(ValueError, match="invalid project_id"):
            ProjectSecretsManager(SecretsManager(), project_id="proj/evil")

    def test_invalid_project_id_dot_dot(self):
        with pytest.raises(ValueError, match="invalid project_id"):
            ProjectSecretsManager(SecretsManager(), project_id="../admin")

    def test_nested_path_normalized(self):
        base = SecretsManager()
        psm = ProjectSecretsManager(base, project_id="myproj")
        result = psm._scoped_path("nested/key/path")
        assert result == "projects/myproj/nested/key/path"


class TestCosignAndGitsignPaths:
    def test_cosign_scoped_path(self):
        result = cosign_scoped_path("myproject", "signing-key")
        assert result == "projects/myproject/cosign/signing-key"

    def test_cosign_invalid_project_id(self):
        with pytest.raises(ValueError, match="invalid project_id"):
            cosign_scoped_path("bad/project", "key")

    def test_cosign_invalid_key_name(self):
        with pytest.raises(ValueError, match="invalid key_name"):
            cosign_scoped_path("myproject", "key with spaces")

    def test_gitsign_scoped_path(self):
        result = gitsign_scoped_path("myproject")
        assert result == "projects/myproject/gitsign/config"

    def test_gitsign_invalid_project_id(self):
        with pytest.raises(ValueError, match="invalid project_id"):
            gitsign_scoped_path("bad/project")


# ── Mount Configuration ──


class TestMountValidation:
    def test_valid_simple_mount(self):
        assert validate_openbao_mount("secret") == "secret"

    def test_valid_nested_mount(self):
        assert validate_openbao_mount("secret/team-a") == "secret/team-a"

    def test_absolute_mount_rejected(self):
        with pytest.raises(ValueError, match="canonical relative"):
            validate_openbao_mount("/secret")

    def test_trailing_slash_rejected(self):
        with pytest.raises(ValueError, match="canonical relative"):
            validate_openbao_mount("secret/")

    def test_reserved_sys_mount_rejected(self):
        with pytest.raises(ValueError, match="system mounts cannot be delegated"):
            validate_openbao_mount("sys")

    def test_reserved_auth_mount_rejected(self):
        with pytest.raises(ValueError, match="system mounts cannot be delegated"):
            validate_openbao_mount("auth/userpass")

    def test_reserved_allowed_with_flag(self):
        result = validate_openbao_mount("sys", allow_reserved=True)
        assert result == "sys"

    def test_backslash_in_mount_rejected(self):
        with pytest.raises(ValueError, match="forbidden characters"):
            validate_openbao_mount("foo\\bar")

    def test_percent_encoding_rejected(self):
        with pytest.raises(ValueError, match="forbidden characters"):
            validate_openbao_mount("foo%2fbar")

    def test_dot_segment_rejected(self):
        with pytest.raises(ValueError, match="invalid or traversal"):
            validate_openbao_mount("secret/../other")

    def test_empty_segment_rejected(self):
        with pytest.raises(ValueError, match="invalid or traversal"):
            validate_openbao_mount("secret//team")


class TestPermittedMounts:
    def test_register_alias_accepted_for_permitted_mount(self):
        mgr = SecretsManager()
        assert mgr.register_alias(SecretAlias(alias="k", path="db/pw", mount="secret")) is None

    def test_register_alias_rejected_for_unlisted_mount(self):
        mgr = SecretsManager()
        with pytest.raises(ValueError, match="not in permitted mounts"):
            mgr.register_alias(SecretAlias(alias="k", path="db/pw", mount="cubbyhole"))


# ── Policy Generation ──


class TestPolicyGeneration:
    def test_render_policy_single_path(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read", "list"}),
        )
        hcl = scope.render_policy("test-policy")
        assert "Gludd scoped policy" in hcl
        assert "test-policy" in hcl
        assert 'path "secret/data/db"' in hcl
        assert '"list", "read"' in hcl

    def test_render_policy_multiple_paths(self):
        scope = OpenBaoPathScope(
            mount="kv",
            paths=("db/creds", "db/config", "ci/*"),
            capabilities=frozenset({"read", "create", "update", "delete"}),
        )
        hcl = scope.render_policy("multi-policy")
        assert 'path "kv/db/creds"' in hcl
        assert 'path "kv/db/config"' in hcl
        assert 'path "kv/ci/*"' in hcl
        assert '"create"' in hcl
        assert '"delete"' in hcl

    def test_render_policy_sort_order(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("zzz/last", "aaa/first"),
            capabilities=frozenset({"read"}),
        )
        hcl = scope.render_policy("sorted-policy")
        aaa_pos = hcl.index("aaa/first")
        zzz_pos = hcl.index("zzz/last")
        assert aaa_pos < zzz_pos

    def test_policy_name_for_agent_valid(self):
        name = policy_name_for_agent("agent-uuid-12345")
        assert name.startswith("gludd-agent-")
        assert len(name) == len("gludd-agent-") + 24

    def test_policy_name_for_agent_deterministic(self):
        a = policy_name_for_agent("agent-1")
        b = policy_name_for_agent("agent-1")
        assert a == b

    def test_policy_name_for_agent_distinct_ids(self):
        a = policy_name_for_agent("agent-alpha")
        b = policy_name_for_agent("agent-beta")
        assert a != b

    def test_policy_name_for_agent_empty_rejected(self):
        with pytest.raises(ValueError):
            policy_name_for_agent("")

    def test_validate_policy_name_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_openbao_policy_name("")

    def test_validate_policy_name_rejects_bad_chars(self):
        with pytest.raises(ValueError):
            validate_openbao_policy_name("bad name!")


# ── Scope Template ──


class TestPathPattern:
    def test_exact_pattern(self):
        pat = _PathPattern.parse("data/db/creds")
        assert pat.segments == ("data", "db", "creds")
        assert pat.subtree is False

    def test_subtree_pattern(self):
        pat = _PathPattern.parse("data/db/*")
        assert pat.segments == ("data", "db")
        assert pat.subtree is True

    def test_render_exact(self):
        pat = _PathPattern.parse("data/db/creds")
        assert pat.render() == "data/db/creds"

    def test_render_subtree(self):
        pat = _PathPattern.parse("data/db/*")
        assert pat.render() == "data/db/*"


class TestScopeIntersection:
    def test_exact_match_intersection(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read", "update", "list"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read", "list"}),
        )
        result = parent.intersect(child)
        assert result.mount == "secret"
        assert result.paths == ("data/db",)
        assert result.capabilities == frozenset({"read", "list"})

    def test_subtree_parent_intersect_child_exact(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/*",),
            capabilities=frozenset({"read", "update"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/db/creds",),
            capabilities=frozenset({"read"}),
        )
        result = parent.intersect(child)
        assert result.paths == ("data/db/creds",)

    def test_exact_parent_intersect_child_subtree_returns_exact(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/db/*",),
            capabilities=frozenset({"read"}),
        )
        result = parent.intersect(child)
        assert result.paths == ("data/db",)
        assert not result.paths[0].endswith("*")

    def test_different_mount_denied(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read"}),
        )
        child = OpenBaoPathScope(
            mount="kv",
            paths=("data/db",),
            capabilities=frozenset({"read"}),
        )
        with pytest.raises(OpenBaoScopeDenied, match="mount aliases do not match"):
            parent.intersect(child)

    def test_no_common_capability_denied(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"update"}),
        )
        with pytest.raises(OpenBaoScopeDenied, match="no common capability"):
            parent.intersect(child)

    def test_subtree_parent_subtree_child_colocated(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/*",),
            capabilities=frozenset({"read"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/*",),
            capabilities=frozenset({"read"}),
        )
        result = parent.intersect(child)
        assert result.paths == ("data/*",)


class TestScopeValidation:
    def test_empty_paths_rejected(self):
        with pytest.raises(ValueError, match=r"1\.\.64 paths"):
            OpenBaoPathScope(
                mount="secret",
                paths=(),
                capabilities=frozenset({"read"}),
            )

    def test_empty_capabilities_rejected(self):
        with pytest.raises(ValueError, match="at least one capability"):
            OpenBaoPathScope(
                mount="secret",
                paths=("data/db",),
                capabilities=frozenset(),
            )

    def test_unknown_capability_rejected(self):
        with pytest.raises(ValueError, match="unsupported capabilities"):
            OpenBaoPathScope(
                mount="secret",
                paths=("data/db",),
                capabilities=frozenset({"admin"}),
            )

    def test_openbao_scope_request_grant(self):
        parent = OpenBaoPathScope(
            mount="secret",
            paths=("data/*",),
            capabilities=frozenset({"read", "list"}),
        )
        child = OpenBaoPathScope(
            mount="secret",
            paths=("data/foo",),
            capabilities=frozenset({"read"}),
        )
        req = OpenBaoScopeRequest(parent=parent, requested=child)
        result = req.grant()
        assert result.paths == ("data/foo",)


class TestScopeEvidence:
    def test_evidence_contains_expected_fields(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read"}),
        )
        evidence = scope.evidence(
            event_type="scope_granted",
            subject_id="agent-001",
            reason_code="ok",
        )
        d = evidence.as_dict()
        assert d["event_type"] == "scope_granted"
        assert d["path_count"] == 1
        assert d["reason_code"] == "ok"
        assert isinstance(d["subject_hash"], str)
        assert isinstance(d["scope_hash"], str)

    def test_evidence_event_types(self):
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/db",),
            capabilities=frozenset({"read"}),
        )
        for event in ("scope_granted", "scope_denied", "scope_revoked"):
            ev = scope.evidence(event_type=event, subject_id="a")
            assert ev.event_type == event


# ── PSK Handling (EnvSecretsManager) ──


class TestEnvSecretsPSKBlocked:
    @patch.dict(os.environ, {"GLUDD_AUTH_PSK": "preshared-secret-12345"}, clear=True)
    def test_psk_is_blocked_by_default(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("GLUDD_AUTH_PSK") is None

    @patch.dict(os.environ, {"GLUDD_SECRET_DB_PASSWORD": "s3cret"}, clear=True)
    def test_gludd_secret_prefix_is_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("GLUDD_SECRET_DB_PASSWORD") == "s3cret"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123"}, clear=True)
    def test_api_key_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("OPENAI_API_KEY") == "sk-test-123"

    @patch.dict(os.environ, {"ZAI_BASE_URL": "https://api.example.com"}, clear=True)
    def test_base_url_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("ZAI_BASE_URL") == "https://api.example.com"

    @patch.dict(os.environ, {"SLURM_AUTH_TOKEN": "tok-abc"}, clear=True)
    def test_auth_token_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("SLURM_AUTH_TOKEN") == "tok-abc"

    @patch.dict(os.environ, {"SERVICE_API_URL": "https://svc.example.com"}, clear=True)
    def test_api_url_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("SERVICE_API_URL") == "https://svc.example.com"

    @patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True)
    def test_path_not_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("PATH") is None

    @patch.dict(os.environ, {"HOME": "/home/user"}, clear=True)
    def test_home_not_allowlisted(self):
        mgr = EnvSecretsManager()
        assert mgr.resolve("HOME") is None

    def test_override_always_honored(self):
        mgr = EnvSecretsManager(overrides={"secret_key_xyz": "override-val"})
        assert mgr.resolve("secret_key_xyz") == "override-val"
        assert "secret_key_xyz" in mgr.list_aliases()


class TestEnvSecretsAllowlistExpansion:
    def test_allow_env_expands_resolution(self):
        mgr = EnvSecretsManager()
        with patch.dict(os.environ, {"CUSTOM_VAR": "value123"}):
            assert mgr.resolve("CUSTOM_VAR") is None
            mgr.allow_env("CUSTOM_VAR")
            assert mgr.resolve("CUSTOM_VAR") == "value123"

    def test_set_override_supersedes_env(self):
        mgr = EnvSecretsManager()
        mgr.set("my_key", "override_value")
        with patch.dict(os.environ, {"MY_KEY": "env_value"}):
            mgr.allow_env("MY_KEY")
            assert mgr.resolve("my_key") == "override_value"

    def test_upper_case_fallback_for_allowlisted(self):
        with patch.dict(os.environ, {"ZAI_API_KEY": "sk-uppercase"}, clear=True):
            mgr = EnvSecretsManager()
            assert mgr.resolve("zai_api_key") == "sk-uppercase"


class TestTTLCap:
    def test_within_bounds_passes_through(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=300, requested_uses=50)
        assert result["ttl_seconds"] == 300
        assert result["uses"] == 50
        assert result["reason"] == "ok"

    def test_ttl_capped(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=1200, requested_uses=1)
        assert result["ttl_seconds"] == 900
        assert result["reason"] == "capped: ttl"

    def test_uses_capped(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=10, requested_uses=500)
        assert result["uses"] == 100
        assert result["reason"] == "capped: uses"

    def test_both_capped(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=1800, requested_uses=999)
        assert result["ttl_seconds"] == 900
        assert result["uses"] == 100
        assert result["reason"] == "capped: ttl+uses"

    def test_negative_ttl_clamped_to_zero(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=-5, requested_uses=10)
        assert result["ttl_seconds"] == 0

    def test_negative_uses_clamped_to_one(self):
        cap = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100)
        result = cap.apply(requested_ttl_seconds=60, requested_uses=-3)
        assert result["uses"] == 1

    def test_invalid_max_ttl_rejected(self):
        with pytest.raises(ValueError, match="must be positive"):
            OpenBaoTTLCap(max_ttl_seconds=0)

    def test_invalid_max_uses_rejected(self):
        with pytest.raises(ValueError, match="must be positive"):
            OpenBaoTTLCap(max_uses=0)


# ── OpenBaoConfig deep ──


class TestOpenBaoConfigDeep:
    def test_approle_ttl_ordering_rejected(self):
        with pytest.raises(ValueError, match="TTL must not exceed"):
            OpenBaoConfig(approle_token_ttl_seconds=7200, approle_token_max_ttl_seconds=3600)

    def test_approle_ttl_equal_allowed(self):
        cfg = OpenBaoConfig(approle_token_ttl_seconds=3600, approle_token_max_ttl_seconds=3600)
        assert cfg.approle_token_ttl_seconds == 3600

    def test_external_tls_verify_string_path(self):
        cfg = OpenBaoConfig(external_tls_verify="/path/to/ca.pem")
        assert cfg.external_tls_verify == "/path/to/ca.pem"

    def test_external_tls_verify_false(self):
        cfg = OpenBaoConfig(external_tls_verify=False)
        assert cfg.external_tls_verify is False

    def test_mode_disabled(self):
        cfg = OpenBaoConfig(mode="disabled")
        assert cfg.mode == "disabled"

    def test_custom_image_digest_pin(self):
        cfg = OpenBaoConfig(local_image_digest_pin="sha256:abc123")
        assert cfg.local_image_digest_pin == "sha256:abc123"
