"""Deep unit tests for the permissions subsystem.

Covers:
- ``InfraAccessPolicy`` — role-based allowlist, default-deny, default_allow_all
- ``load_infra_access_policy`` — built-in defaults + overrides
- ``ToolPermission`` / ``ToolPermissionSpec`` — rule construction, wildcard matching
- ``CapabilityLattice`` — built-in chain, native actions, inheritance, custom chains
- ``PermissionEvaluator`` — deny-wins, allow+lattice, wildcard bypass, scope matching,
  convenience methods, bulk evaluation, infra delegation
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.permissions.infra_access import (
    InfraAccessPolicy,
    load_infra_access_policy,
)
from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    PermissionEvaluator,
    ToolAction,
    ToolPermission,
    ToolPermissionSpec,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _eval() -> PermissionEvaluator:
    return PermissionEvaluator(lattice=CapabilityLattice())


def _spec(role: str, *permissions: ToolPermission) -> ToolPermissionSpec:
    return ToolPermissionSpec(role=role, permissions=tuple(permissions))


_Actions = tuple[str, ...]


def _tp(
    tool: str,
    allowed: _Actions = (),
    denied: _Actions = (),
    scope: str | None = None,
) -> ToolPermission:
    return ToolPermission(tool=tool, allowed_actions=allowed, denied_actions=denied, scope=scope)


# ── InfraAccessPolicy ────────────────────────────────────────────────────────


class TestInfraAccessPolicy:
    def test_can_deploy_allowed_role(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin", "operator"}))
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("operator") is True

    def test_can_deploy_denied_role(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy("viewer") is False

    def test_can_destroy_allowed_role(self) -> None:
        policy = InfraAccessPolicy(allowed_destroy_roles=frozenset({"admin", "infra_destroy"}))
        assert policy.can_destroy("infra_destroy") is True

    def test_can_destroy_denied_role(self) -> None:
        policy = InfraAccessPolicy(allowed_destroy_roles=frozenset({"admin"}))
        assert policy.can_destroy("reader") is False

    def test_empty_string_role_denied(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy("") is False
        assert policy.can_destroy("") is False

    def test_none_role_denied(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy(None) is False  # type: ignore[arg-type]
        assert policy.can_destroy(None) is False  # type: ignore[arg-type]

    def test_non_string_role_denied(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy(42) is False  # type: ignore[arg-type]

    def test_default_allow_all_grants_when_set_empty(self) -> None:
        policy = InfraAccessPolicy(default_allow_all=True)
        assert policy.can_deploy("anyone") is True
        assert policy.can_destroy("anyone") is True

    def test_default_allow_all_does_not_bypass_explicit_set(self) -> None:
        policy = InfraAccessPolicy(
            allowed_deploy_roles=frozenset({"admin"}),
            default_allow_all=True,
        )
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("viewer") is False

    def test_default_deny_when_both_sets_empty_and_flag_false(self) -> None:
        policy = InfraAccessPolicy()
        assert policy.can_deploy("admin") is False
        assert policy.can_destroy("admin") is False

    def test_frozen_dataclass_prevents_mutation(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        with pytest.raises(FrozenInstanceError):
            policy.allowed_deploy_roles = frozenset()  # type: ignore[misc]


# ── load_infra_access_policy ─────────────────────────────────────────────────


class TestLoadInfraAccessPolicy:
    def test_loads_builtin_defaults(self) -> None:
        policy = load_infra_access_policy()
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("operator") is True
        assert policy.can_deploy("infra_deploy") is True
        assert policy.can_destroy("infra_destroy") is True

    def test_loads_with_deploy_override(self) -> None:
        policy = load_infra_access_policy(deploy_roles=frozenset({"superadmin"}))
        assert policy.can_deploy("superadmin") is True
        assert policy.can_deploy("admin") is False

    def test_loads_with_destroy_override(self) -> None:
        policy = load_infra_access_policy(destroy_roles=frozenset({"superadmin"}))
        assert policy.can_destroy("superadmin") is True
        assert policy.can_destroy("infra_destroy") is False

    def test_loads_with_both_overrides(self) -> None:
        policy = load_infra_access_policy(
            deploy_roles=frozenset({"d"}),
            destroy_roles=frozenset({"x"}),
        )
        assert policy.can_deploy("d") is True
        assert policy.can_destroy("x") is True
        assert policy.can_deploy("admin") is False


# ── ToolPermission / ToolPermissionSpec ──────────────────────────────────────


class TestToolPermission:
    def test_string_args_are_converted_to_tuples(self) -> None:
        tp = ToolPermission(tool="read_file", allowed_actions=("read", "write"), denied_actions=("delete",))
        assert isinstance(tp.allowed_actions, tuple)
        assert isinstance(tp.denied_actions, tuple)

    def test_frozen_prevents_mutation(self) -> None:
        tp = ToolPermission(tool="bash", allowed_actions=("execute",))
        with pytest.raises(FrozenInstanceError):
            tp.tool = "other"  # type: ignore[misc]


class TestToolPermissionSpec:
    def test_permissions_for_exact_tool_match(self) -> None:
        spec = _spec("writer", _tp("read_file", ("read",)), _tp("bash", ("execute",)))
        result = spec.permissions_for("bash")
        assert len(result) == 1
        assert result[0].tool == "bash"

    def test_permissions_for_wildcard_match(self) -> None:
        spec = _spec("admin", _tp("*", ("read", "write", "execute", "delete")))
        result = spec.permissions_for("any_tool")
        assert len(result) == 1
        assert result[0].tool == "*"

    def test_permissions_for_returns_empty_on_no_match(self) -> None:
        spec = _spec("reader", _tp("read_file", ("read",)))
        assert spec.permissions_for("bash") == []

    def test_permissions_for_returns_multiple_matches(self) -> None:
        spec = _spec("coder", _tp("bash", ("read",)), _tp("bash", ("execute",)))
        result = spec.permissions_for("bash")
        assert len(result) == 2


# ── CapabilityLattice ────────────────────────────────────────────────────────


class TestCapabilityLatticeBuiltin:
    def test_reader_has_read_and_nothing_else(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("reader")
        assert ToolAction.READ in actions
        assert ToolAction.WRITE not in actions
        assert ToolAction.EXECUTE not in actions
        assert ToolAction.DELETE not in actions

    def test_writer_inherits_read_from_reader(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("writer")
        assert ToolAction.WRITE in actions
        assert ToolAction.CREATE in actions
        assert ToolAction.OVERWRITE in actions
        assert ToolAction.READ in actions  # inherited

    def test_coder_inherits_read_and_write(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("coder")
        assert ToolAction.EXECUTE in actions
        assert ToolAction.READ in actions
        assert ToolAction.WRITE in actions

    def test_admin_has_all_actions(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("admin")
        assert ToolAction.DELETE in actions
        assert ToolAction.EXECUTE in actions
        assert ToolAction.WRITE in actions
        assert ToolAction.READ in actions

    def test_viewer_has_read_standalone(self) -> None:
        lattice = CapabilityLattice()
        actions = lattice.all_actions("viewer")
        assert actions == {ToolAction.READ}

    def test_unknown_role_infers_action_from_name(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.all_actions("publisher") == frozenset({"publish"})

    def test_is_granted_for_native(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("reader", ToolAction.READ) is True

    def test_is_granted_for_inherited(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("coder", ToolAction.READ) is True

    def test_is_granted_denies_ungranted_action(self) -> None:
        lattice = CapabilityLattice()
        assert lattice.is_granted("reader", ToolAction.DELETE) is False


class TestCapabilityLatticeCustom:
    def test_custom_chain_infers_actions_from_role_name(self) -> None:
        chain = {"editor": frozenset({"viewer"}), "viewer": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        assert "edit" in lattice.all_actions("editor")
        assert "view" in lattice.all_actions("viewer")

    def test_custom_chain_inherits_transitively(self) -> None:
        chain = {"admin": frozenset({"editor"}), "editor": frozenset({"viewer"}), "viewer": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        assert "view" in lattice.all_actions("admin")


# ── PermissionEvaluator ──────────────────────────────────────────────────────


class TestPermissionEvaluatorDenyWins:
    def test_deny_overrides_allow(self) -> None:
        spec = _spec("writer", _tp("read_file", ("read",), ("read",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "read_file", ToolAction.READ) is False

    def test_deny_not_matching_action_does_not_override(self) -> None:
        spec = _spec("writer", _tp("read_file", ("read", "write"), ("delete",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "read_file", ToolAction.READ) is True


class TestPermissionEvaluatorAllowLattice:
    def test_explicit_allow_passes_lattice_check(self) -> None:
        spec = _spec("coder", _tp("bash", ("execute",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE) is True

    def test_allow_blocked_by_lattice_when_role_lacks_action(self) -> None:
        spec = _spec("reader", _tp("bash", ("execute",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE) is False

    def test_allow_passes_when_role_has_inherited_action(self) -> None:
        spec = _spec("coder", _tp("read_file", ("read",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "read_file", ToolAction.READ) is True

    def test_default_deny_when_no_allow_rule(self) -> None:
        spec = _spec("admin", _tp("read_file", ("read",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE) is False


class TestPermissionEvaluatorWildcard:
    def test_wildcard_allow_skips_lattice(self) -> None:
        spec = _spec("reader", _tp("*", ("*",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "any_tool", "any_action") is True

    def test_wildcard_allow_still_subject_to_deny(self) -> None:
        spec = _spec("reader", _tp("*", ("*",), ("delete",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "any_tool", "delete") is False

    def test_wildcard_in_deny_overrides_wildcard_in_allow(self) -> None:
        spec = _spec("reader", _tp("*", ("*",), ("read",)))
        evaluator = _eval()
        assert evaluator.may_use(spec, "read_file", ToolAction.READ) is False
        assert evaluator.may_use(spec, "read_file", ToolAction.WRITE) is True


class TestPermissionEvaluatorScopeMatching:
    def test_none_scope_matches_any_request(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), scope=None))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:gludd") is True

    def test_exact_scope_match_permits(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), scope="project:gludd"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:gludd") is True

    def test_mismatched_scope_denies(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), scope="project:gludd"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:other") is False

    def test_project_wildcard_matches_any_project(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), scope="project:*"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:foo") is True
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:bar") is True

    def test_project_wildcard_does_not_match_non_project_scope(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), scope="project:*"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="global:any") is False

    def test_none_request_scope_denied_when_rule_has_scope(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), scope="project:gludd"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE) is False

    def test_deny_rule_scope_restricts_deny(self) -> None:
        spec = _spec("admin", _tp("bash", ("execute",), ("execute",), scope="project:gludd"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:gludd") is False
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:other") is False


class TestPermissionEvaluatorConvenience:
    def test_may_read(self) -> None:
        spec = _spec("reader", _tp("read_file", ("read",)))
        assert _eval().may_read(spec, "read_file") is True

    def test_may_write(self) -> None:
        spec = _spec("writer", _tp("write_file", ("write",)))
        assert _eval().may_write(spec, "write_file") is True

    def test_may_execute(self) -> None:
        spec = _spec("coder", _tp("bash", ("execute",)))
        assert _eval().may_execute(spec, "bash") is True


class TestPermissionEvaluatorBulk:
    def test_evaluate_all_returns_verdicts_for_all_queries(self) -> None:
        spec = _spec("coder", _tp("read_file", ("read",)), _tp("bash", ("execute",)))
        evaluator = _eval()
        result = evaluator.evaluate_all(
            spec,
            [("read_file", "read"), ("bash", "execute"), ("bash", "delete")],
        )
        assert result[("read_file", "read")] is True
        assert result[("bash", "execute")] is True
        assert result[("bash", "delete")] is False

    def test_evaluate_all_respects_scope(self) -> None:
        spec = _spec("coder", _tp("bash", ("execute",), scope="project:gludd"))
        evaluator = _eval()
        result = evaluator.evaluate_all(
            spec,
            [("bash", "execute")],
            scope="project:other",
        )
        assert result[("bash", "execute")] is False


class TestPermissionEvaluatorInfraDelegation:
    def test_may_deploy_uses_default_policy(self) -> None:
        evaluator = _eval()
        assert evaluator.may_deploy("admin") is True
        assert evaluator.may_deploy("viewer") is False

    def test_may_destroy_uses_default_policy(self) -> None:
        evaluator = _eval()
        assert evaluator.may_destroy("admin") is True
        assert evaluator.may_destroy("reader") is False

    def test_may_deploy_with_custom_infra_policy(self) -> None:
        infra = InfraAccessPolicy(allowed_deploy_roles=frozenset({"super"}))
        evaluator = PermissionEvaluator(lattice=CapabilityLattice(), infra_policy=infra)
        assert evaluator.may_deploy("super") is True
        assert evaluator.may_deploy("admin") is False

    def test_permission_intersection_role_cross(self) -> None:
        spec = _spec("writer", _tp("read_file", ("read",)), _tp("write_file", ("write",)))
        evaluator = _eval()
        assert evaluator.may_read(spec, "read_file") is True
        assert evaluator.may_write(spec, "write_file") is True
        assert evaluator.may_execute(spec, "write_file") is False


class TestPermissionEvaluatorCombined:
    def test_multiple_permission_rules_deny_then_allow(self) -> None:
        spec = _spec(
            "coder",
            _tp("bash", denied=("delete",)),
            _tp("bash", ("read", "write", "execute")),
        )
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE) is True
        assert evaluator.may_use(spec, "bash", ToolAction.READ) is True
        assert evaluator.may_use(spec, "bash", ToolAction.DELETE) is False

    def test_wildcard_tool_with_scope_and_lattice(self) -> None:
        spec = _spec("coder", _tp("*", ("execute",), scope="project:gludd"))
        evaluator = _eval()
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:gludd") is True
        assert evaluator.may_use(spec, "bash", ToolAction.EXECUTE, scope="project:other") is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
