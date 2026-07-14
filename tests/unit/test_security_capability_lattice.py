"""Unit tests for security/capability_lattice.py — role capabilities and dispatch gating."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.security.capability_lattice import (
    _BUILTIN,
    CapabilityError,
    ProtectedPathError,
    RoleCapabilities,
    capabilities_for,
    check_dispatch,
    check_self_modification,
    is_collections_path,
    is_protected_path,
    role_may_dispatch,
)


class TestRoleCapabilities:
    def test_default_deny(self) -> None:
        caps = RoleCapabilities()
        assert caps.role == "<unknown>"
        assert caps.collections_self_modify is False
        assert caps.dispatch_kinds == frozenset()

    def test_named_role_stores_role(self) -> None:
        caps = RoleCapabilities(role="coder")
        assert caps.role == "coder"

    def test_granted_capabilities_stored(self) -> None:
        caps = RoleCapabilities(
            role="admin",
            collections_self_modify=True,
            dispatch_kinds=frozenset({"role", "skill"}),
        )
        assert caps.collections_self_modify is True
        assert "role" in caps.dispatch_kinds

    def test_frozen_immutable(self) -> None:
        caps = RoleCapabilities(role="test")
        with pytest.raises(FrozenInstanceError):
            caps.role = "other"  # type: ignore[misc]


class TestBuiltins:
    def test_self_improve_can_self_modify(self) -> None:
        assert _BUILTIN["self_improve_agent"].collections_self_modify is True
        assert "collection" in _BUILTIN["self_improve_agent"].dispatch_kinds

    def test_coder_cannot_self_modify(self) -> None:
        caps = _BUILTIN["coder"]
        assert caps.collections_self_modify is False
        assert "collection" not in caps.dispatch_kinds

    def test_operator_cannot_self_modify(self) -> None:
        assert _BUILTIN["operator"].collections_self_modify is False

    def test_event_loop_cannot_dispatch_collection(self) -> None:
        assert "collection" not in _BUILTIN["event_loop"].dispatch_kinds

    def test_report_status_limited_dispatch(self) -> None:
        caps = _BUILTIN["report_status"]
        assert caps.dispatch_kinds == frozenset({"skill"})

    def test_all_roles_defined(self) -> None:
        assert len(_BUILTIN) == 7
        expected = {
            "self_improve_agent",
            "self_research_agent",
            "coder",
            "operator",
            "report_status",
            "security_auditor",
            "event_loop",
        }
        assert set(_BUILTIN.keys()) == expected


class TestCapabilitiesFor:
    def test_known_role_returns_caps(self) -> None:
        caps = capabilities_for("self_improve_agent")
        assert caps.collections_self_modify is True

    def test_unknown_role_returns_baseline(self) -> None:
        caps = capabilities_for("nonexistent_role")
        assert caps.collections_self_modify is False
        assert caps.dispatch_kinds == frozenset()

    def test_none_returns_baseline(self) -> None:
        caps = capabilities_for(None)
        assert caps.role == "<unknown>"

    def test_empty_string_returns_baseline(self) -> None:
        caps = capabilities_for("")
        assert caps.role == "<unknown>"

    def test_non_string_returns_baseline(self) -> None:
        caps = capabilities_for(42)  # type: ignore[arg-type]
        assert caps.role == "<unknown>"

    def test_strips_whitespace(self) -> None:
        caps = capabilities_for("  coder  ")
        assert caps.role == "coder"
        assert "mcp" in caps.dispatch_kinds


class TestRoleMayDispatch:
    def test_coder_may_dispatch_mcp(self) -> None:
        assert role_may_dispatch("coder", "mcp") is True

    def test_coder_may_not_dispatch_collection(self) -> None:
        assert role_may_dispatch("coder", "collection") is False

    def test_self_improve_may_dispatch_collection(self) -> None:
        assert role_may_dispatch("self_improve_agent", "collection") is True

    def test_unknown_role_denied(self) -> None:
        assert role_may_dispatch("ghost", "mcp") is False

    def test_none_role_denied(self) -> None:
        assert role_may_dispatch(None, "role") is False

    def test_unknown_kind_denied(self) -> None:
        assert role_may_dispatch("coder", "unknown_kind") is False


class TestCheckDispatch:
    def test_valid_dispatch_passes(self) -> None:
        check_dispatch("coder", "role")

    def test_invalid_dispatch_raises(self) -> None:
        with pytest.raises(CapabilityError) as ctx:
            check_dispatch("coder", "collection")
        assert "lacks the capability" in str(ctx.value)

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(CapabilityError):
            check_dispatch("ghost", "mcp")


class TestIsCollectionsPath:
    def test_collections_path_detected(self) -> None:
        assert is_collections_path(
            "/repo/collections/ansible_collections/general_ludd/agent/plugins/modules/foo.py"
        ) is True

    def test_non_collections_path(self) -> None:
        assert is_collections_path("/repo/src/general_ludd/foo.py") is False

    def test_collections_component_midpath(self) -> None:
        assert is_collections_path("src/collections/foo.py") is True


class TestIsProtectedPath:
    def test_guardrails_file_protected(self) -> None:
        assert is_protected_path("src/guardrails.py") is True

    def test_capability_lattice_protected(self) -> None:
        assert is_protected_path("security/capability_lattice.py") is True

    def test_permissions_file_protected(self) -> None:
        assert is_protected_path("config/permissions.py") is True

    def test_ordinary_file_not_protected(self) -> None:
        assert is_protected_path("src/agents/capabilities.py") is False

    def test_denied_path_detected(self) -> None:
        assert is_protected_path(".claude/hooks/test.py") is True

    def test_agents_md_protected(self) -> None:
        assert is_protected_path("/repo/agents.md") is True

    def test_enforce_make_stem_protected(self) -> None:
        assert is_protected_path("enforce_make.py") is True


class TestCheckSelfModification:
    def test_protected_path_blocked_for_any_role(self) -> None:
        with pytest.raises(ProtectedPathError):
            check_self_modification("/repo/guardrails.py", "self_improve_agent")

    def test_collections_path_blocked_for_coder(self) -> None:
        with pytest.raises(CapabilityError):
            check_self_modification(
                "collections/general_ludd/agent/plugins/modules/test.py", "coder"
            )

    def test_collections_path_allowed_for_self_improve(self) -> None:
        check_self_modification(
            "collections/general_ludd/agent/plugins/modules/test.py",
            "self_improve_agent",
        )

    def test_non_collections_non_protected_allowed(self) -> None:
        check_self_modification("src/general_ludd/agents/capabilities.py", "coder")
