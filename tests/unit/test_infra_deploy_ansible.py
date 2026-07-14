"""Unit tests for infra access policy and ansible infra deploy/destroy modules."""

from __future__ import annotations

from general_ludd.permissions.infra_access import (
    InfraAccessPolicy,
    load_infra_access_policy,
)


class TestInfraAccessPolicy:
    def test_default_policy_denies_unknown_role(self) -> None:
        policy = InfraAccessPolicy()
        assert not policy.can_deploy("viewer")
        assert not policy.can_destroy("viewer")

    def test_default_policy_denies_empty_role(self) -> None:
        policy = InfraAccessPolicy()
        assert not policy.can_deploy("")
        assert not policy.can_destroy("")

    def test_default_policy_denies_none_role(self) -> None:
        policy = InfraAccessPolicy()
        assert not policy.can_deploy(None)  # type: ignore[arg-type]
        assert not policy.can_destroy(None)  # type: ignore[arg-type]

    def test_deploy_allowlist_grants_listed_role(self) -> None:
        policy = InfraAccessPolicy(
            allowed_deploy_roles=frozenset({"admin", "operator"}),
        )
        assert policy.can_deploy("admin")
        assert policy.can_deploy("operator")
        assert not policy.can_deploy("viewer")

    def test_destroy_allowlist_grants_listed_role(self) -> None:
        policy = InfraAccessPolicy(
            allowed_destroy_roles=frozenset({"admin", "infra_destroy"}),
        )
        assert policy.can_destroy("admin")
        assert policy.can_destroy("infra_destroy")
        assert not policy.can_destroy("viewer")

    def test_deploy_and_destroy_allowlists_independent(self) -> None:
        policy = InfraAccessPolicy(
            allowed_deploy_roles=frozenset({"infra_deploy"}),
            allowed_destroy_roles=frozenset({"infra_destroy"}),
        )
        assert policy.can_deploy("infra_deploy")
        assert not policy.can_deploy("infra_destroy")
        assert policy.can_destroy("infra_destroy")
        assert not policy.can_destroy("infra_deploy")

    def test_default_allow_all_when_empty(self) -> None:
        policy = InfraAccessPolicy(default_allow_all=True)
        assert policy.can_deploy("anyrole")
        assert policy.can_destroy("anyrole")

    def test_default_allow_all_does_not_override_explicit_allowlist(self) -> None:
        policy = InfraAccessPolicy(
            allowed_deploy_roles=frozenset({"admin"}),
            allowed_destroy_roles=frozenset({"admin"}),
            default_allow_all=True,
        )
        assert policy.can_deploy("admin")
        assert not policy.can_deploy("viewer")
        assert policy.can_destroy("admin")
        assert not policy.can_destroy("viewer")

    def test_frozen_dataclass_prevents_mutation(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        raised = False
        try:
            policy.allowed_deploy_roles = frozenset()  # type: ignore[misc]
        except Exception:
            raised = True
        assert raised, "frozen dataclass must prevent mutation"


class TestLoadInfraAccessPolicy:
    def test_load_builtin_defaults(self) -> None:
        policy = load_infra_access_policy()
        assert policy.can_deploy("admin")
        assert policy.can_deploy("operator")
        assert policy.can_deploy("infra_deploy")
        assert not policy.can_deploy("viewer")

        assert policy.can_destroy("admin")
        assert policy.can_destroy("operator")
        assert policy.can_destroy("infra_destroy")
        assert not policy.can_destroy("viewer")

    def test_load_with_override_deploy_roles(self) -> None:
        policy = load_infra_access_policy(
            deploy_roles=frozenset({"custom_deploy"}),
        )
        assert policy.can_deploy("custom_deploy")
        assert not policy.can_deploy("admin")
        # destroy roles should still be default
        assert policy.can_destroy("admin")

    def test_load_with_override_destroy_roles(self) -> None:
        policy = load_infra_access_policy(
            destroy_roles=frozenset({"custom_destroy"}),
        )
        assert policy.can_destroy("custom_destroy")
        assert not policy.can_destroy("infra_destroy")
        # deploy roles should still be default
        assert policy.can_deploy("admin")

    def test_load_with_both_overrides(self) -> None:
        policy = load_infra_access_policy(
            deploy_roles=frozenset({"d1", "d2"}),
            destroy_roles=frozenset({"d3"}),
        )
        assert policy.can_deploy("d1")
        assert policy.can_deploy("d2")
        assert not policy.can_deploy("admin")
        assert policy.can_destroy("d3")
        assert not policy.can_destroy("admin")

    def test_load_returns_new_instance_each_call(self) -> None:
        a = load_infra_access_policy()
        b = load_infra_access_policy()
        assert a is not b
        assert a.allowed_deploy_roles == b.allowed_deploy_roles

    def test_strips_whitespace_from_role(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy("  admin  ")


class TestInfraAccessPolicyModuleShape:
    """Structural checks ensuring the policy can be wired into the daemon."""

    def test_module_exports(self) -> None:
        from general_ludd.permissions import infra_access

        assert hasattr(infra_access, "InfraAccessPolicy")
        assert hasattr(infra_access, "load_infra_access_policy")

    def test_policy_can_be_imported_from_permissions(self) -> None:
        from general_ludd.permissions.infra_access import InfraAccessPolicy

        policy = InfraAccessPolicy()
        assert isinstance(policy, InfraAccessPolicy)
