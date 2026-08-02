"""D-15: OpenBao token scope narrowing tests — SEC.1 control verification.

Verifies mount alias validation, path traversal rejection, scope intersection,
policy HCL rendering, TTL capping, use-limit enforcement, and revocation
evidence. Ensures child scope is always the monotonic intersection of parent
and requested.
"""

from __future__ import annotations

import pytest

from general_ludd.secrets.openbao_scope import (
    _MAX_MOUNT_CHARS,
    _MAX_PATH_CHARS,
    _MAX_SCOPE_PATHS,
    OpenBaoPathScope,
    OpenBaoScopeDenied,
    OpenBaoScopeRequest,
    OpenBaoTTLCap,
    policy_name_for_agent,
    validate_openbao_mount,
    validate_openbao_path,
    validate_openbao_policy_name,
)

# ── Mount validation ──


class TestMountValidation:
    def test_valid_simple_mount(self) -> None:
        assert validate_openbao_mount("secret") == "secret"

    def test_valid_nested_mount(self) -> None:
        assert validate_openbao_mount("secret/team-a") == "secret/team-a"

    def test_empty_mount_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("")

    def test_absolute_mount_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("/secret")

    def test_trailing_slash_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret/")

    def test_dot_segment_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret/../auth")

    def test_null_byte_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret\x00extra")

    def test_backslash_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret\\extra")

    def test_percent_encoding_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret%2fextra")

    def test_system_mount_rejected_by_default(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("sys")

    def test_system_mount_allowed_with_flag(self) -> None:
        assert validate_openbao_mount("sys", allow_reserved=True) == "sys"

    def test_auth_mount_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("auth")

    def test_exceeds_max_length(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("s" * (_MAX_MOUNT_CHARS + 1))


# ── Path validation ──


class TestPathValidation:
    def test_valid_simple_path(self) -> None:
        assert validate_openbao_path("data/foo", allow_terminal_wildcard=True) == "data/foo"

    def test_valid_terminal_wildcard(self) -> None:
        assert validate_openbao_path("data/*", allow_terminal_wildcard=True) == "data/*"

    def test_non_terminal_wildcard_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/*/extra", allow_terminal_wildcard=True)

    def test_wildcard_rejected_when_disallowed(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/*", allow_terminal_wildcard=False)

    def test_traversal_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/../secret", allow_terminal_wildcard=True)

    def test_absolute_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("/data/foo", allow_terminal_wildcard=True)

    def test_null_byte_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/foo\x00bar", allow_terminal_wildcard=True)

    def test_exceeds_max_length(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("d" * (_MAX_PATH_CHARS + 1), allow_terminal_wildcard=True)


# ── Policy name validation ──


class TestPolicyNameValidation:
    def test_valid_name(self) -> None:
        assert validate_openbao_policy_name("gludd-agent-abc123") == "gludd-agent-abc123"

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name("a" * 129)

    def test_invalid_chars_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name("bad/name")


# ── Scope construction ──


class TestScopeConstruction:
    def test_valid_simple_scope(self) -> None:
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/foo", "data/bar"),
            capabilities={"read"},
        )
        assert scope.mount == "secret"
        assert scope.paths == ("data/bar", "data/foo")  # sorted
        assert scope.capabilities == frozenset({"read"})

    def test_empty_paths_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoPathScope(mount="secret", paths=(), capabilities={"read"})

    def test_empty_capabilities_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoPathScope(mount="secret", paths=("data/foo",), capabilities=set())

    def test_unknown_capability_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoPathScope(mount="secret", paths=("data/foo",), capabilities={"sudo"})

    def test_too_many_paths_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoPathScope(
                mount="secret",
                paths=tuple(f"data/p{i}" for i in range(_MAX_SCOPE_PATHS + 1)),
                capabilities={"read"},
            )

    def test_traversal_in_mount_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoPathScope(mount="secret/../auth", paths=("data/foo",), capabilities={"read"})

    def test_dedup_sorted_paths(self) -> None:
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/foo", "data/foo", "data/bar"),
            capabilities={"read"},
        )
        assert scope.paths == ("data/bar", "data/foo")

    def test_valid_all_capabilities(self) -> None:
        scope = OpenBaoPathScope(
            mount="secret",
            paths=("data/*",),
            capabilities={"create", "delete", "list", "patch", "read", "update"},
        )
        assert len(scope.capabilities) == 6


# ── Scope intersection ──


class TestScopeIntersection:
    def test_identical_scopes_intersect(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read", "list"})
        child = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read", "list"})
        intersection = parent.intersect(child)
        assert intersection.paths == ("data/*",)
        assert intersection.capabilities == frozenset({"list", "read"})

    def test_child_narrower_capabilities(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read", "list"})
        child = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        intersection = parent.intersect(child)
        assert intersection.capabilities == frozenset({"read"})

    def test_child_narrower_paths(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="secret", paths=("data/foo",), capabilities={"read"})
        intersection = parent.intersect(child)
        assert intersection.paths == ("data/foo",)

    def test_child_sibling_path_denied(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="secret", paths=("other/foo",), capabilities={"read"})
        with pytest.raises(OpenBaoScopeDenied, match="no common path"):
            parent.intersect(child)

    def test_different_mount_denied(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="kv", paths=("data/*",), capabilities={"read"})
        with pytest.raises(OpenBaoScopeDenied, match="mount"):
            parent.intersect(child)

    def test_no_common_capability_denied(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"delete"})
        with pytest.raises(OpenBaoScopeDenied, match="capability"):
            parent.intersect(child)

    def test_monotonic_intersection_tags(self) -> None:
        """Ensure intersection is monotonic: int(A, B) is subset of A and B."""
        parent = OpenBaoPathScope(mount="secret", paths=("data/*", "config/*"), capabilities={"read", "list"})
        child = OpenBaoPathScope(mount="secret", paths=("data/foo", "config/*"), capabilities={"read"})
        ix = parent.intersect(child)
        assert ix.capabilities <= parent.capabilities
        assert ix.capabilities <= child.capabilities


# ── Scope request ──


class TestScopeRequest:
    def test_grant_returns_intersection(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read", "list"})
        child = OpenBaoPathScope(mount="secret", paths=("data/foo",), capabilities={"read"})
        request = OpenBaoScopeRequest(parent=parent, requested=child)
        granted = request.grant()
        assert granted.paths == ("data/foo",)
        assert granted.capabilities == frozenset({"read"})

    def test_grant_rejects_denied_mount(self) -> None:
        parent = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        child = OpenBaoPathScope(mount="kv", paths=("data/foo",), capabilities={"read"})
        request = OpenBaoScopeRequest(parent=parent, requested=child)
        with pytest.raises(OpenBaoScopeDenied):
            request.grant()


# ── Policy HCL rendering ──


class TestPolicyRendering:
    def test_basic_policy_rendering(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        hcl = scope.render_policy("gludd-agent-test")
        assert 'path "secret/data/*"' in hcl
        assert 'capabilities = ["read"]' in hcl
        assert 'Gludd scoped policy "gludd-agent-test"' in hcl

    def test_multi_path_rendering(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("data/*", "config/foo"), capabilities={"read", "list"})
        hcl = scope.render_policy("gludd-agent-multi")
        assert "secret/data/*" in hcl
        assert "secret/config/foo" in hcl
        assert "[cs]" in hcl or "list" in hcl


# ── Policy name for agent ──


class TestPolicyNameForAgent:
    def test_deterministic_name(self) -> None:
        a = policy_name_for_agent("agent-42")
        b = policy_name_for_agent("agent-42")
        assert a == b

    def test_name_prefix(self) -> None:
        name = policy_name_for_agent("agent-42")
        assert name.startswith("gludd-agent-")

    def test_agent_id_not_leaked(self) -> None:
        name = policy_name_for_agent("agent-super-secret-12345")
        assert "super-secret" not in name
        assert "agent-42" not in name

    def test_empty_agent_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            policy_name_for_agent("")


# ── Scope evidence ──


class TestScopeEvidence:
    def test_scope_granted_evidence(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        evidence = scope.evidence(event_type="scope_granted", subject_id="agent-42")
        assert evidence.event_type == "scope_granted"
        assert evidence.path_count == 1
        assert evidence.capabilities == ("read",)
        assert evidence.reason_code == "ok"

    def test_scope_denied_evidence(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        evidence = scope.evidence(event_type="scope_denied", subject_id="agent-42", reason_code="no_common_path")
        assert evidence.event_type == "scope_denied"
        assert evidence.reason_code == "no_common_path"

    def test_subject_hash_does_not_disclose_id(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        evidence = scope.evidence(event_type="scope_granted", subject_id="agent-super-secret-12345")
        assert "super-secret" not in evidence.subject_hash
        assert "12345" not in evidence.subject_hash

    def test_scope_hash_is_deterministic(self) -> None:
        a = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        b = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        assert (
            a.evidence(event_type="scope_granted", subject_id="x").scope_hash
            == b.evidence(event_type="scope_granted", subject_id="x").scope_hash
        )

    def test_evidence_as_dict_has_required_fields(self) -> None:
        scope = OpenBaoPathScope(mount="secret", paths=("data/*",), capabilities={"read"})
        d = scope.evidence(event_type="scope_revoked", subject_id="agent-42").as_dict()
        for field in ("event_type", "subject_hash", "scope_hash", "path_count", "capabilities", "reason_code"):
            assert field in d


# ── TTL cap ──


class TestOpenBaoTTLCap:
    def test_default_ttl_cap(self) -> None:
        cap = OpenBaoTTLCap()
        assert cap.max_ttl_seconds == 900
        assert cap.max_uses == 100

    def test_custom_ttl_cap(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=300, max_uses=5)
        assert cap.max_ttl_seconds == 300
        assert cap.max_uses == 5

    def test_ttl_below_ceiling_accepted(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=600)
        result = cap.apply(requested_ttl_seconds=300, requested_uses=50)
        assert result["ttl_seconds"] == 300
        assert result["uses"] == 50

    def test_ttl_above_ceiling_capped(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=600)
        result = cap.apply(requested_ttl_seconds=1200, requested_uses=50)
        assert result["ttl_seconds"] == 600

    def test_uses_above_ceiling_capped(self) -> None:
        cap = OpenBaoTTLCap(max_uses=10)
        result = cap.apply(requested_ttl_seconds=300, requested_uses=500)
        assert result["uses"] == 10

    def test_negative_ttl_clamped_to_zero(self) -> None:
        cap = OpenBaoTTLCap()
        result = cap.apply(requested_ttl_seconds=-1, requested_uses=1)
        assert result["ttl_seconds"] == 0

    def test_zero_uses_clamped_to_one(self) -> None:
        cap = OpenBaoTTLCap()
        result = cap.apply(requested_ttl_seconds=300, requested_uses=0)
        assert result["uses"] == 1

    def test_invalid_ttl_cap_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoTTLCap(max_ttl_seconds=0)

    def test_invalid_uses_cap_rejected(self) -> None:
        with pytest.raises(ValueError):
            OpenBaoTTLCap(max_uses=0)

    def test_evidence_with_reason_code(self) -> None:
        cap = OpenBaoTTLCap()
        result = cap.apply(requested_ttl_seconds=3600, requested_uses=500)
        assert result["reason"] == "capped: ttl+uses"

    def test_ttl_cap_below_spec_max(self) -> None:
        cap = OpenBaoTTLCap()
        assert cap.max_ttl_seconds <= 900  # spec says "short TTL"
        assert cap.max_uses <= 100
