from __future__ import annotations

from general_ludd.permissions.infra_access import InfraAccessPolicy, load_infra_access_policy


class TestInfraAccessPolicy:
    def test_default_all_roles_denied(self) -> None:
        policy = InfraAccessPolicy()
        assert policy.can_deploy("admin") is False
        assert policy.can_destroy("operator") is False

    def test_can_deploy_when_role_in_allowlist(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin", "operator"}))
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("operator") is True

    def test_can_deploy_when_role_not_in_allowlist(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy("viewer") is False
        assert policy.can_deploy("operator") is False

    def test_can_destroy_when_role_in_allowlist(self) -> None:
        policy = InfraAccessPolicy(allowed_destroy_roles=frozenset({"admin"}))
        assert policy.can_destroy("admin") is True

    def test_can_destroy_when_role_not_in_allowlist(self) -> None:
        policy = InfraAccessPolicy(allowed_destroy_roles=frozenset({"admin"}))
        assert policy.can_destroy("operator") is False

    def test_default_allow_all_enabled(self) -> None:
        policy = InfraAccessPolicy(default_allow_all=True)
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("viewer") is True
        assert policy.can_destroy("operator") is True

    def test_default_allow_all_not_active_when_roles_specified(self) -> None:
        policy = InfraAccessPolicy(
            allowed_deploy_roles=frozenset({"admin"}),
            default_allow_all=True,
        )
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("viewer") is False

    def test_empty_role_denied(self) -> None:
        policy = InfraAccessPolicy(
            allowed_deploy_roles=frozenset({"admin"}),
            allowed_destroy_roles=frozenset({"operator"}),
        )
        assert policy.can_deploy("") is False
        assert policy.can_destroy("") is False

    def test_non_string_role_denied(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy(None) is False  # type: ignore[arg-type]

    def test_role_stripped_on_check(self) -> None:
        policy = InfraAccessPolicy(allowed_deploy_roles=frozenset({"admin"}))
        assert policy.can_deploy(" admin ") is True
        assert policy.can_deploy("admin ") is True


class TestLoadInfraAccessPolicy:
    def test_load_with_no_overrides_uses_builtins(self) -> None:
        policy = load_infra_access_policy()
        assert policy.can_deploy("admin") is True
        assert policy.can_deploy("infra_deploy") is True
        assert policy.can_deploy("viewer") is False
        assert policy.can_destroy("admin") is True
        assert policy.can_destroy("infra_destroy") is True
        assert policy.can_destroy("viewer") is False

    def test_load_with_deploy_override(self) -> None:
        policy = load_infra_access_policy(
            deploy_roles=frozenset({"superadmin"}),
        )
        assert policy.can_deploy("superadmin") is True
        assert policy.can_deploy("admin") is False

    def test_load_with_destroy_override(self) -> None:
        policy = load_infra_access_policy(
            destroy_roles=frozenset({"root"}),
        )
        assert policy.can_destroy("root") is True
        assert policy.can_destroy("admin") is False

    def test_load_with_both_overrides(self) -> None:
        policy = load_infra_access_policy(
            deploy_roles=frozenset({"a"}),
            destroy_roles=frozenset({"b"}),
        )
        assert policy.can_deploy("a") is True
        assert policy.can_destroy("b") is True
        assert policy.can_deploy("admin") is False
        assert policy.can_destroy("admin") is False
