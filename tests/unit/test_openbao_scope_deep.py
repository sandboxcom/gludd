"""Deep tests for untested openbao_scope components: TTLCap, validators, path intersection."""

from __future__ import annotations

import pytest

from general_ludd.secrets.openbao_scope import (
    OpenBaoTTLCap,
    _digest,
    _intersect_pattern,
    _PathPattern,
    policy_name_for_agent,
    validate_openbao_mount,
    validate_openbao_path,
    validate_openbao_policy_name,
)

# ── validate_openbao_mount ──────────────────────────────────────────────


class TestValidateOpenBaoMount:
    def test_valid_simple_mount(self) -> None:
        assert validate_openbao_mount("secret") == "secret"

    def test_valid_nested_mount(self) -> None:
        assert validate_openbao_mount("secret/team-a") == "secret/team-a"

    def test_strips_trailing_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("secret/")

    def test_rejects_leading_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("/secret")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_openbao_mount("secret\\team")

    def test_rejects_percent_encoded(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_openbao_mount("secret%2fteam")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_openbao_mount("sec\x00ret")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("")

    def test_rejects_dot_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_openbao_mount("secret/../sys")

    def test_rejects_dotdot_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_openbao_mount("secret/..")

    def test_rejects_empty_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_openbao_mount("secret//team")

    def test_rejects_reserved_mount_auth(self) -> None:
        with pytest.raises(ValueError, match="system mounts"):
            validate_openbao_mount("auth/token")

    def test_rejects_reserved_mount_sys(self) -> None:
        with pytest.raises(ValueError, match="system mounts"):
            validate_openbao_mount("sys/policy")

    def test_rejects_reserved_mount_cubbyhole(self) -> None:
        with pytest.raises(ValueError, match="system mounts"):
            validate_openbao_mount("cubbyhole")

    def test_allows_reserved_with_flag(self) -> None:
        assert validate_openbao_mount("auth/token", allow_reserved=True) == "auth/token"

    def test_rejects_over_max_chars(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("a" * 129)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount(123)  # type: ignore[arg-type]

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_mount("   ")


# ── validate_openbao_path ───────────────────────────────────────────────


class TestValidateOpenBaoPath:
    def test_valid_simple_path(self) -> None:
        assert validate_openbao_path("data/tenants/acme", allow_terminal_wildcard=False) == "data/tenants/acme"

    def test_valid_with_wildcard(self) -> None:
        assert validate_openbao_path("data/tenants/*", allow_terminal_wildcard=True) == "data/tenants/*"

    def test_rejects_wildcard_when_disallowed(self) -> None:
        with pytest.raises(ValueError, match="wildcard"):
            validate_openbao_path("data/tenants/*", allow_terminal_wildcard=False)

    def test_rejects_midpath_wildcard(self) -> None:
        with pytest.raises(ValueError, match="wildcard"):
            validate_openbao_path("data/*/acme", allow_terminal_wildcard=True)

    def test_rejects_leading_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("/data/tenants", allow_terminal_wildcard=False)

    def test_rejects_trailing_slash(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("data/tenants/", allow_terminal_wildcard=False)

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_openbao_path("data\\tenants", allow_terminal_wildcard=False)

    def test_rejects_percent_encoded(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_openbao_path("data%2f..%2ftenants", allow_terminal_wildcard=False)

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_openbao_path("da\x00ta", allow_terminal_wildcard=False)

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("", allow_terminal_wildcard=False)

    def test_rejects_dot_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_openbao_path("data/./tenants", allow_terminal_wildcard=False)

    def test_rejects_dotdot_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_openbao_path("data/../sys", allow_terminal_wildcard=False)

    def test_rejects_empty_segment(self) -> None:
        with pytest.raises(ValueError, match="traversal"):
            validate_openbao_path("data//tenants", allow_terminal_wildcard=False)

    def test_rejects_over_max_chars(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path("a" * 513, allow_terminal_wildcard=False)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_path(None, allow_terminal_wildcard=False)  # type: ignore[arg-type]


# ── validate_openbao_policy_name ────────────────────────────────────────


class TestValidateOpenBaoPolicyName:
    def test_valid_simple_name(self) -> None:
        assert validate_openbao_policy_name("gludd-agent-a1") == "gludd-agent-a1"

    def test_valid_max_length(self) -> None:
        assert validate_openbao_policy_name("a" * 128) == "a" * 128

    def test_rejects_over_max_length(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name("a" * 129)

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name("")

    def test_accepts_leading_digit(self) -> None:
        assert validate_openbao_policy_name("123agent") == "123agent"

    def test_rejects_special_chars(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name("agent/a1")

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name(None)  # type: ignore[arg-type]

    def test_rejects_spaces(self) -> None:
        with pytest.raises(ValueError):
            validate_openbao_policy_name("agent a1")


# ── OpenBaoTTLCap ───────────────────────────────────────────────────────


class TestOpenBaoTTLCap:
    def test_default_construction(self) -> None:
        cap = OpenBaoTTLCap()
        assert cap.max_ttl_seconds == 900
        assert cap.max_uses == 100

    def test_custom_construction(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=60, max_uses=5)
        assert cap.max_ttl_seconds == 60
        assert cap.max_uses == 5

    def test_rejects_zero_max_ttl(self) -> None:
        with pytest.raises(ValueError, match="max_ttl"):
            OpenBaoTTLCap(max_ttl_seconds=0)

    def test_rejects_negative_max_ttl(self) -> None:
        with pytest.raises(ValueError, match="max_ttl"):
            OpenBaoTTLCap(max_ttl_seconds=-1)

    def test_rejects_zero_max_uses(self) -> None:
        with pytest.raises(ValueError, match="max_uses"):
            OpenBaoTTLCap(max_uses=0)

    def test_rejects_negative_max_uses(self) -> None:
        with pytest.raises(ValueError, match="max_uses"):
            OpenBaoTTLCap(max_uses=-5)

    def test_apply_within_limits_returns_ok(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(600, 50)
        assert result == {"ttl_seconds": 600, "uses": 50, "reason": "ok"}

    def test_apply_caps_ttl_only(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(1200, 50)
        assert result == {"ttl_seconds": 900, "uses": 50, "reason": "capped: ttl"}

    def test_apply_caps_uses_only(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(600, 200)
        assert result == {"ttl_seconds": 600, "uses": 100, "reason": "capped: uses"}

    def test_apply_caps_both_ttl_and_uses(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(1200, 200)
        assert result == {"ttl_seconds": 900, "uses": 100, "reason": "capped: ttl+uses"}

    def test_apply_clamps_negative_ttl_to_zero(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(-100, 50)
        assert result["ttl_seconds"] == 0

    def test_apply_clamps_zero_uses_to_one(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(600, 0)
        assert result["uses"] == 1

    def test_apply_with_zero_max_constrains_everything(self) -> None:
        cap = OpenBaoTTLCap(max_ttl_seconds=1, max_uses=1)
        result = cap.apply(3600, 1000)
        assert result == {"ttl_seconds": 1, "uses": 1, "reason": "capped: ttl+uses"}

    def test_apply_float_ttl_is_truncated(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(599.9, 50)
        assert result["ttl_seconds"] == 599

    def test_apply_at_exact_bounds_is_ok(self) -> None:
        result = OpenBaoTTLCap(max_ttl_seconds=900, max_uses=100).apply(900, 100)
        assert result == {"ttl_seconds": 900, "uses": 100, "reason": "ok"}


# ── _PathPattern parsing and intersection ───────────────────────────────


class TestPathPattern:
    def test_parse_simple_path(self) -> None:
        pat = _PathPattern.parse("data/tenants/acme")
        assert pat.segments == ("data", "tenants", "acme")
        assert pat.subtree is False
        assert pat.render() == "data/tenants/acme"

    def test_parse_wildcard_path(self) -> None:
        pat = _PathPattern.parse("data/tenants/*")
        assert pat.segments == ("data", "tenants")
        assert pat.subtree is True
        assert pat.render() == "data/tenants/*"

    def test_parse_single_segment_wildcard(self) -> None:
        pat = _PathPattern.parse("*")
        assert pat.segments == ()
        assert pat.subtree is True
        assert pat.render() == "/*"

    def test_parse_root_like(self) -> None:
        pat = _PathPattern.parse("data")
        assert pat.segments == ("data",)
        assert pat.subtree is False


class TestIntersectPattern:
    def test_identical_exact_paths_intersect(self) -> None:
        left = _PathPattern.parse("data/tenants/acme")
        right = _PathPattern.parse("data/tenants/acme")
        result = _intersect_pattern(left, right)
        assert result is not None
        assert result.render() == "data/tenants/acme"
        assert result.subtree is False

    def test_identical_wildcard_intersect_yields_wildcard(self) -> None:
        left = _PathPattern.parse("data/tenants/*")
        right = _PathPattern.parse("data/tenants/*")
        result = _intersect_pattern(left, right)
        assert result is not None
        assert result.render() == "data/tenants/*"
        assert result.subtree is True

    def test_parent_wildcard_intersect_child_exact_yields_child(self) -> None:
        left = _PathPattern.parse("data/tenants/*")
        right = _PathPattern.parse("data/tenants/acme")
        result = _intersect_pattern(left, right)
        assert result is not None
        assert result.render() == "data/tenants/acme"
        assert result.subtree is False

    def test_parent_wildcard_intersect_child_wildcard_yields_child(self) -> None:
        left = _PathPattern.parse("data/tenants/*")
        right = _PathPattern.parse("data/tenants/acme/*")
        result = _intersect_pattern(left, right)
        assert result is not None
        assert result.render() == "data/tenants/acme/*"
        assert result.subtree is True

    def test_child_exact_no_match_to_parent_without_wildcard(self) -> None:
        left = _PathPattern.parse("data/tenants/acme")
        right = _PathPattern.parse("data/tenants/acme/agents/a1")
        result = _intersect_pattern(left, right)
        assert result is None

    def test_disjoint_paths_no_intersection(self) -> None:
        left = _PathPattern.parse("data/tenants/acme")
        right = _PathPattern.parse("data/tenants/other")
        result = _intersect_pattern(left, right)
        assert result is None

    def test_wildcard_does_not_match_shorter_path(self) -> None:
        left = _PathPattern.parse("data/tenants/acme/*")
        right = _PathPattern.parse("data/tenants")
        result = _intersect_pattern(left, right)
        assert result is None

    def test_wildcard_order_independent(self) -> None:
        a = _PathPattern.parse("data/tenants/*")
        b = _PathPattern.parse("data/tenants/acme/agents/a1")
        assert _intersect_pattern(a, b) is not None
        assert _intersect_pattern(b, a) is not None


# ── _digest and policy_name_for_agent ───────────────────────────────────


class TestDigest:
    def test_digest_is_deterministic(self) -> None:
        a = _digest("domain", "value")
        b = _digest("domain", "value")
        assert a == b
        assert len(a) == 32

    def test_digest_domain_separation_matters(self) -> None:
        a = _digest("dom1", "value")
        b = _digest("dom2", "value")
        assert a != b

    def test_digest_value_matters(self) -> None:
        a = _digest("dom", "v1")
        b = _digest("dom", "v2")
        assert a != b

    def test_digest_hex_format(self) -> None:
        result = _digest("gludd-openbao-policy", "agent-42")
        assert all(c in "0123456789abcdef" for c in result)


class TestPolicyNameForAgent:
    def test_policy_name_is_stable(self) -> None:
        a = policy_name_for_agent("agent-42")
        b = policy_name_for_agent("agent-42")
        assert a == b
        assert a.startswith("gludd-agent-")
        assert len(a) == len("gludd-agent-") + 24

    def test_different_agents_produce_different_names(self) -> None:
        a = policy_name_for_agent("agent-1")
        b = policy_name_for_agent("agent-2")
        assert a != b

    def test_policy_name_never_exposes_agent_id(self) -> None:
        name = policy_name_for_agent("super-secret-agent-007")
        assert "super-secret" not in name
        assert "007" not in name

    def test_rejects_empty_agent_id(self) -> None:
        with pytest.raises(ValueError):
            policy_name_for_agent("")

    def test_rejects_overly_long_agent_id(self) -> None:
        with pytest.raises(ValueError):
            policy_name_for_agent("a" * 513)

    def test_rejects_non_string(self) -> None:
        with pytest.raises(ValueError):
            policy_name_for_agent(None)  # type: ignore[arg-type]

    def test_policy_name_passes_validator(self) -> None:
        name = policy_name_for_agent("test-agent")
        assert validate_openbao_policy_name(name) == name
