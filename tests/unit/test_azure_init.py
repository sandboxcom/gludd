"""Package-level tests for general_ludd.azure __init__.py —

verify clean import, __all__ completeness, re-export integrity,
submodule accessibility, and cross-import consistency.
"""

from __future__ import annotations

import importlib
from dataclasses import is_dataclass

import pytest

# ── package-level imports ───────────────────────────────────────────────

ALL_EXPECTED = [
    "AZURE_BUILTIN_ROLES",
    "AZURE_EXPERT_ROLES",
    "AZURE_RESOURCE_PROVIDERS",
    "DEFAULT_CIDR",
    "FORBIDDEN_SUFFIX_PATTERNS",
    "KNOWN_RBAC_ACTIONS",
    "PERSONA_ROLE_MAP",
    "PROVIDER_OPERATIONS",
    "SECRET_ACTION_PATTERNS",
    "AcrConfig",
    "AzureRbacRole",
    "ContainerAppDeployConfig",
    "IamAssignment",
    "LogAnalyticsQuery",
    "NetworkDesign",
    "PricingResult",
    "acr_registry_config",
    "audit_existing_assignments",
    "audit_iam_assignments",
    "check_security_critical_denials",
    "container_app_config",
    "design_azure_network",
    "design_vnet",
    "generate_nsg_rules",
    "generate_role_definition",
    "inventory_resources",
    "optimize_cost",
    "query_log_analytics",
    "recommend_roles_for_persona",
    "validate_action_string",
    "validate_against_azure_schema",
    "validate_rbac_role_definition",
]

CALLABLES = {
    "acr_registry_config",
    "audit_existing_assignments",
    "audit_iam_assignments",
    "check_security_critical_denials",
    "container_app_config",
    "design_azure_network",
    "design_vnet",
    "generate_nsg_rules",
    "generate_role_definition",
    "inventory_resources",
    "optimize_cost",
    "query_log_analytics",
    "recommend_roles_for_persona",
    "validate_action_string",
    "validate_against_azure_schema",
    "validate_rbac_role_definition",
}

DATACLASSES = {
    "AcrConfig",
    "AzureRbacRole",
    "ContainerAppDeployConfig",
    "IamAssignment",
    "LogAnalyticsQuery",
    "NetworkDesign",
    "PricingResult",
}

DICT_CONSTANTS = {
    "AZURE_BUILTIN_ROLES",
    "AZURE_EXPERT_ROLES",
    "AZURE_RESOURCE_PROVIDERS",
    "PERSONA_ROLE_MAP",
    "PROVIDER_OPERATIONS",
    "SECRET_ACTION_PATTERNS",
}

FROZENSET_CONSTANTS = {
    "KNOWN_RBAC_ACTIONS",
}

STR_CONSTANTS = {
    "DEFAULT_CIDR",
}

TUPLE_CONSTANTS = {
    "FORBIDDEN_SUFFIX_PATTERNS",
}

AZURE_SUBMODULES = [
    "contracts",
    "core",
    "iam_advisor",
    "network_designer",
    "rbac_validator",
]

SUBMODULE_KEY_SYMBOLS = {
    "contracts": [
        "AcrConfig",
        "AzureRbacRole",
        "ContainerAppDeployConfig",
        "IamAssignment",
        "LogAnalyticsQuery",
        "NetworkDesign",
        "PricingResult",
    ],
    "core": [
        "AZURE_EXPERT_ROLES",
        "acr_registry_config",
        "audit_iam_assignments",
        "container_app_config",
        "design_azure_network",
        "optimize_cost",
    ],
    "iam_advisor": ["PERSONA_ROLE_MAP", "audit_existing_assignments", "recommend_roles_for_persona"],
    "network_designer": ["DEFAULT_CIDR", "design_vnet", "generate_nsg_rules"],
    "rbac_validator": [
        "AZURE_BUILTIN_ROLES",
        "KNOWN_RBAC_ACTIONS",
        "validate_action_string",
        "check_security_critical_denials",
        "generate_role_definition",
    ],
}


# ── Package import basics ────────────────────────────────────────────────


class TestAzurePackageImport:
    def test_package_imports_cleanly(self) -> None:
        import general_ludd.azure as az

        assert az is not None

    def test_package_name_and_file(self) -> None:
        import general_ludd.azure as az

        assert az.__name__ == "general_ludd.azure"
        assert az.__file__ is not None
        assert az.__file__.endswith("__init__.py")

    def test_package_path_is_directory(self) -> None:
        import general_ludd.azure as az

        assert az.__path__ is not None
        assert len(az.__path__) >= 1

    def test_package_has_docstring(self) -> None:
        import general_ludd.azure as az

        assert az.__doc__ is not None
        assert len(az.__doc__) > 0
        assert "Azure" in az.__doc__


# ── __all__ completeness ─────────────────────────────────────────────────


class TestAllCompleteness:
    def test_all_exists(self) -> None:
        import general_ludd.azure as az

        assert isinstance(az.__all__, list)

    def test_all_length_matches_expected(self) -> None:
        import general_ludd.azure as az

        assert len(az.__all__) == len(ALL_EXPECTED)
        assert set(az.__all__) == set(ALL_EXPECTED)

    def test_every_all_entry_accessible_as_attr(self) -> None:
        import general_ludd.azure as az

        for name in az.__all__:
            assert hasattr(az, name), f"__all__ entry {name!r} not found as attribute"

    def test_no_extra_public_names_beyond_all(self) -> None:
        import general_ludd.azure as az

        public = {n for n in dir(az) if not n.startswith("_") and n != "__all__"}
        all_set = set(az.__all__)
        extra = public - all_set
        assert extra == set(), f"public names not in __all__: {extra}"


# ── Re-export type fidelity ──────────────────────────────────────────────


class TestReExportTypes:
    @pytest.mark.parametrize("name", sorted(CALLABLES))
    def test_callables_are_callable(self, name: str) -> None:
        import general_ludd.azure as az

        obj = getattr(az, name)
        assert callable(obj), f"{name} should be callable"

    @pytest.mark.parametrize("name", sorted(DATACLASSES))
    def test_dataclasses_are_dataclasses(self, name: str) -> None:
        import general_ludd.azure as az

        obj = getattr(az, name)
        assert is_dataclass(obj), f"{name} should be a dataclass"

    @pytest.mark.parametrize("name", sorted(DICT_CONSTANTS))
    def test_dict_constants_are_dicts(self, name: str) -> None:
        import general_ludd.azure as az

        obj = getattr(az, name)
        assert isinstance(obj, dict), f"{name} should be a dict"
        assert len(obj) > 0, f"{name} should be non-empty"

    @pytest.mark.parametrize("name", sorted(FROZENSET_CONSTANTS))
    def test_frozenset_constants_are_frozensets(self, name: str) -> None:
        import general_ludd.azure as az

        obj = getattr(az, name)
        assert isinstance(obj, frozenset), f"{name} should be a frozenset"
        assert len(obj) > 0, f"{name} should be non-empty"

    def test_default_cidr_is_string(self) -> None:
        import general_ludd.azure as az

        assert isinstance(az.DEFAULT_CIDR, str)
        assert "/" in az.DEFAULT_CIDR

    def test_forbidden_suffix_patterns_is_tuple(self) -> None:
        import general_ludd.azure as az

        assert isinstance(az.FORBIDDEN_SUFFIX_PATTERNS, tuple)
        assert len(az.FORBIDDEN_SUFFIX_PATTERNS) > 0

    @pytest.mark.parametrize("name", sorted(CALLABLES))
    def test_callable_signatures_have_doc(self, name: str) -> None:
        import general_ludd.azure as az

        obj = getattr(az, name)
        assert obj.__doc__ is not None, f"{name} has no docstring"
        assert len(obj.__doc__) > 0, f"{name} has empty docstring"


# ── Submodule accessibility ──────────────────────────────────────────────


class TestSubmoduleAccessibility:
    @pytest.mark.parametrize("submodule", AZURE_SUBMODULES)
    def test_submodule_importable(self, submodule: str) -> None:
        full = f"general_ludd.azure.{submodule}"
        mod = importlib.import_module(full)
        assert mod is not None
        assert mod.__name__ == full

    @pytest.mark.parametrize(
        "submodule,expected_symbols", [(sm, symbols) for sm, symbols in SUBMODULE_KEY_SYMBOLS.items()]
    )
    def test_key_symbols_present_in_submodule(self, submodule: str, expected_symbols: list[str]) -> None:
        full = f"general_ludd.azure.{submodule}"
        mod = importlib.import_module(full)
        for sym in expected_symbols:
            assert hasattr(mod, sym), f"{sym} missing from {full}"


# ── Namespace integrity ──────────────────────────────────────────────────


class TestNamespaceIntegrity:
    def test_repeated_import_idempotent(self) -> None:
        a = __import__("general_ludd.azure")
        b = __import__("general_ludd.azure")
        assert a is b

    def test_submodule_access_via_package(self) -> None:
        import general_ludd.azure as az
        import general_ludd.azure.core as core

        assert az.core is core

    @pytest.mark.parametrize("submodule", AZURE_SUBMODULES)
    def test_submodule_accessible_as_package_attr(self, submodule: str) -> None:
        import general_ludd.azure as az

        assert hasattr(az, submodule), f"submodule {submodule} not accessible as package attr"

    def test_cross_import_consistency(self) -> None:
        import general_ludd.azure as az
        import general_ludd.azure.contracts as ct

        assert az.AcrConfig is ct.AcrConfig
        assert az.NetworkDesign is ct.NetworkDesign

    def test_cross_import_core_consistency(self) -> None:
        import general_ludd.azure as az
        import general_ludd.azure.core as core

        assert az.acr_registry_config is core.acr_registry_config
        assert az.AZURE_EXPERT_ROLES is core.AZURE_EXPERT_ROLES
        assert az.validate_rbac_role_definition is core.validate_rbac_role_definition

    def test_cross_import_iam_consistency(self) -> None:
        import general_ludd.azure as az
        import general_ludd.azure.iam_advisor as ia

        assert az.PERSONA_ROLE_MAP is ia.PERSONA_ROLE_MAP
        assert az.recommend_roles_for_persona is ia.recommend_roles_for_persona
        assert az.audit_existing_assignments is ia.audit_existing_assignments

    def test_cross_import_network_consistency(self) -> None:
        import general_ludd.azure as az
        import general_ludd.azure.network_designer as nd

        assert az.DEFAULT_CIDR is nd.DEFAULT_CIDR
        assert az.design_vnet is nd.design_vnet
        assert az.generate_nsg_rules is nd.generate_nsg_rules

    def test_cross_import_rbac_consistency(self) -> None:
        import general_ludd.azure as az
        import general_ludd.azure.rbac_validator as rv

        assert az.AZURE_BUILTIN_ROLES is rv.AZURE_BUILTIN_ROLES
        assert az.AZURE_RESOURCE_PROVIDERS is rv.AZURE_RESOURCE_PROVIDERS
        assert az.KNOWN_RBAC_ACTIONS is rv.KNOWN_RBAC_ACTIONS
        assert az.PROVIDER_OPERATIONS is rv.PROVIDER_OPERATIONS
        assert az.SECRET_ACTION_PATTERNS is rv.SECRET_ACTION_PATTERNS
        assert az.FORBIDDEN_SUFFIX_PATTERNS is rv.FORBIDDEN_SUFFIX_PATTERNS
        assert az.validate_action_string is rv.validate_action_string
        assert az.check_security_critical_denials is rv.check_security_critical_denials
        assert az.generate_role_definition is rv.generate_role_definition
        assert az.validate_against_azure_schema is rv.validate_against_azure_schema


# ── Smoke: callable functions return expected shapes ─────────────────────


class TestReExportSmoke:
    def test_acr_registry_config_smoke(self) -> None:
        import general_ludd.azure as az

        result = az.acr_registry_config("smoke-acr", "Basic", "eastus")
        assert result["status"] == "ok"

    def test_recommend_roles_for_persona_smoke(self) -> None:
        import general_ludd.azure as az

        roles = az.recommend_roles_for_persona("developer")
        assert "Contributor" in roles

    def test_design_vnet_smoke(self) -> None:
        import general_ludd.azure as az

        design = az.design_vnet("smoke-vnet")
        assert design.vnet_name == "smoke-vnet"

    def test_generate_nsg_rules_smoke(self) -> None:
        import general_ludd.azure as az

        rules = az.generate_nsg_rules()
        assert len(rules) >= 3

    def test_validate_action_string_smoke(self) -> None:
        import general_ludd.azure as az

        ok, _msg = az.validate_action_string("Microsoft.Compute/virtualMachines/read")
        assert ok is True

    def test_check_security_critical_denials_smoke(self) -> None:
        import general_ludd.azure as az

        missing = az.check_security_critical_denials([])
        assert isinstance(missing, list)
        assert len(missing) > 0

    def test_generate_role_definition_smoke(self) -> None:
        import general_ludd.azure as az

        role = az.generate_role_definition(
            "Smoke Role",
            "Smoke test role with at least twenty chars",
            ["Microsoft.Resources"],
        )
        assert role["Name"] == "Smoke Role"
        assert "Actions" in role

    def test_validate_against_azure_schema_smoke(self) -> None:
        import general_ludd.azure as az

        ok, _msgs = az.validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A test role with enough characters for validation",
                "Actions": ["Microsoft.Compute/virtualMachines/read"],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/sub-id"],
            }
        )
        assert ok is True


# ── Edge: re-exported dict constants are non-empty ───────────────────────


class TestConstantFidelity:
    def test_azure_builtin_roles_has_known_entry(self) -> None:
        import general_ludd.azure as az

        assert "Contributor" in az.AZURE_BUILTIN_ROLES
        assert "Reader" in az.AZURE_BUILTIN_ROLES
        assert "Owner" in az.AZURE_BUILTIN_ROLES

    def test_azure_expert_roles_has_eight_entries(self) -> None:
        import general_ludd.azure as az

        assert len(az.AZURE_EXPERT_ROLES) == 8

    def test_persona_role_map_has_four_personas(self) -> None:
        import general_ludd.azure as az

        assert len(az.PERSONA_ROLE_MAP) == 4
        for persona in ("developer", "operator", "auditor", "admin"):
            assert persona in az.PERSONA_ROLE_MAP

    def test_known_rbac_actions_has_compute_read(self) -> None:
        import general_ludd.azure as az

        assert "Microsoft.Compute/virtualMachines/read" in az.KNOWN_RBAC_ACTIONS

    def test_provider_operations_has_network(self) -> None:
        import general_ludd.azure as az

        assert "Microsoft.Network" in az.PROVIDER_OPERATIONS


# ── Edge: re-exported dataclasses are instantiable at package level ─────


class TestDataclassInstantiation:
    def test_acr_config_from_package(self) -> None:
        import general_ludd.azure as az

        cfg = az.AcrConfig(name="test-acr", sku="Basic")
        assert cfg.name == "test-acr"
        assert cfg.sku == "Basic"
        assert cfg.admin_enabled is False
        assert isinstance(cfg, az.AcrConfig)

    def test_azure_rbac_role_from_package(self) -> None:
        import general_ludd.azure as az

        role = az.AzureRbacRole(name="Reader", description="Read-only")
        assert role.name == "Reader"
        assert isinstance(role, az.AzureRbacRole)

    def test_iam_assignment_from_package(self) -> None:
        import general_ludd.azure as az

        assignment = az.IamAssignment(persona="dev", role_name="Contributor", scope="/")
        assert assignment.is_builtin is True
        assert isinstance(assignment, az.IamAssignment)

    def test_network_design_from_package(self) -> None:
        import general_ludd.azure as az

        nd = az.NetworkDesign(vnet_name="vnet1", address_space="10.0.0.0/16")
        assert nd.vnet_name == "vnet1"
        assert isinstance(nd, az.NetworkDesign)

    def test_container_app_deploy_config_from_package(self) -> None:
        import general_ludd.azure as az

        cfg = az.ContainerAppDeployConfig(
            name="ca-test",
            image="mcr.io/test:latest",
            cpu="4.0",
            memory="16Gi",
        )
        assert cfg.name == "ca-test"
        assert cfg.min_replicas == 0

    def test_log_analytics_query_from_package(self) -> None:
        import general_ludd.azure as az

        q = az.LogAnalyticsQuery(workspace_id="ws-01", query="Heartbeat")
        assert q.workspace_id == "ws-01"
        assert q.timespan == "P1D"

    def test_pricing_result_from_package(self) -> None:
        import general_ludd.azure as az

        pr = az.PricingResult(
            service_type="container_apps",
            region="eastus",
            hourly_rate=0.62,
            monthly_estimate=452.6,
        )
        assert pr.hourly_rate == 0.62
        assert pr.monthly_estimate == 452.6
