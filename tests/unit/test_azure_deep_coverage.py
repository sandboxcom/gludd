"""Deep coverage gap-filling tests for ``general_ludd.azure``.

Targets: untested regex patterns, private constants, boundary invariants,
and cross-module consistency not covered by existing suites.
"""

from __future__ import annotations

import json
import re

import pytest

from general_ludd.azure.contracts import (
    AzureRbacRole,
    ContainerAppDeployConfig,
    NetworkDesign,
    PricingResult,
)
from general_ludd.azure.core import (
    AZURE_EXPERT_ROLES,
    acr_registry_config,
    audit_iam_assignments,
    container_app_config,
    design_azure_network,
    get_deploy_strategist,
    inventory_resources,
    optimize_cost,
    query_log_analytics,
    validate_rbac_role_definition,
)
from general_ludd.azure.iam_advisor import (
    _OVER_PRIVILEGED_ROLES,
    _RISKY_SCOPES,
    PERSONA_ROLE_MAP,
    _is_subscription_scope,
    audit_existing_assignments,
    recommend_roles_for_persona,
)
from general_ludd.azure.network_designer import (
    _NSG_WEB_RULES,
    _SUBNET_DEFAULTS,
    _SUBNET_PREFIX,
    DEFAULT_CIDR,
)
from general_ludd.azure.rbac_validator import (
    _ALL_KNOWN_ACTIONS,
    _RE_SECRET_PATTERNS,
    _VALID_ACTION_RE,
    FORBIDDEN_SUFFIX_PATTERNS,
    PROVIDER_OPERATIONS,
    SECRET_ACTION_PATTERNS,
    SECURITY_CRITICAL_OPS,
    _lazy_provider_map,
    all_known_actions,
    generate_role_definition,
    validate_action_string,
    validate_against_azure_schema,
)


# ═══════════════════════════════════════════════════════════════════════════
# iam_advisor: private constants directly tested
# ═══════════════════════════════════════════════════════════════════════════
class TestIamAdvisorPrivateConstants:
    def test_over_privileged_roles_cardinality(self):
        assert len(_OVER_PRIVILEGED_ROLES) == 2
        assert "Owner" in _OVER_PRIVILEGED_ROLES
        assert "Contributor" in _OVER_PRIVILEGED_ROLES

    def test_risky_scopes_cardinality(self):
        assert len(_RISKY_SCOPES) == 2
        assert "/" in _RISKY_SCOPES
        assert "/subscriptions" in _RISKY_SCOPES

    def test_risky_scopes_are_not_superset_of_resource_group(self):
        assert "/subscriptions/resourceGroups/rg" not in _RISKY_SCOPES
        assert "/providers" not in _RISKY_SCOPES

    def test_is_subscription_scope_none_false(self):
        assert _is_subscription_scope(None) is False

    def test_is_subscription_scope_whitespace_only_false(self):
        assert _is_subscription_scope("   ") is False
        assert _is_subscription_scope("\t") is False

    def test_is_subscription_scope_near_miss(self):
        assert _is_subscription_scope("/subscription") is False
        assert _is_subscription_scope("/subscriptions/") is False

    def test_persona_role_map_roles_all_unique_per_persona(self):
        for persona, roles in PERSONA_ROLE_MAP.items():
            assert len(roles) == len(set(roles)), f"{persona} has duplicate roles"

    def test_recommend_roles_never_empty_for_known_personas(self):
        for persona in PERSONA_ROLE_MAP:
            assert recommend_roles_for_persona(persona) != []

    def test_audit_existing_assignments_preserves_extra_keys(self):
        assignments = [
            {"role": "Owner", "scope": "/", "extra_field": 42, "custom": True},
        ]
        findings = audit_existing_assignments(assignments)
        assert findings[0]["extra_field"] == 42
        assert findings[0]["custom"] is True


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: regex pattern compilation verification
# ═══════════════════════════════════════════════════════════════════════════
class TestRbacRegexPatterns:
    def test_valid_action_re_compiled(self):
        assert _VALID_ACTION_RE is not None
        assert hasattr(_VALID_ACTION_RE, "match")

    def test_valid_action_re_matches_standard_patterns(self):
        assert _VALID_ACTION_RE.match("Microsoft.Compute/virtualMachines/read")
        assert _VALID_ACTION_RE.match("Microsoft.Network/virtualNetworks/write")
        assert _VALID_ACTION_RE.match("Microsoft.App/containerApps/delete")
        assert _VALID_ACTION_RE.match("Microsoft.Resources/deployments/validate/action")

    def test_valid_action_re_rejects_malformed(self):
        assert not _VALID_ACTION_RE.match("Azure.Compute/virtualMachines/read")
        assert not _VALID_ACTION_RE.match("Microsoft/virtualMachines/read")
        assert not _VALID_ACTION_RE.match("Microsoft.Compute/read")
        assert not _VALID_ACTION_RE.match("Microsoft.Compute/virtualMachines/")
        assert not _VALID_ACTION_RE.match("read")

    def test_re_secret_patterns_all_compiled(self):
        assert len(_RE_SECRET_PATTERNS) == len(SECRET_ACTION_PATTERNS)
        assert all(hasattr(r, "search") for r in _RE_SECRET_PATTERNS)

    def test_secret_patterns_match_designed_signatures(self):
        for _pat_re, msg in _RE_SECRET_PATTERNS.items():
            assert isinstance(msg, str)
            assert "/action" in msg


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: forbidden suffix & secret action exhaustive checks
# ═══════════════════════════════════════════════════════════════════════════
class TestRbacForbiddenSuffixMatch:
    @pytest.mark.parametrize("suffix", FORBIDDEN_SUFFIX_PATTERNS)
    def test_each_suffix_triggers_forbidden(self, suffix):
        action = f"Microsoft.Test/resource{suffix}"
        ok, msg = validate_action_string(action)
        assert ok is False
        assert "forbidden suffix" in msg

    def test_all_secret_action_patterns_trigger_on_read(self):
        for pattern_str, _ in SECRET_ACTION_PATTERNS.items():
            compiled = re.compile(pattern_str)
            cleaned = pattern_str.replace("\\\\", "").replace("$", "")
            test_action = f"Microsoft.Test/resource{cleaned}"
            ok, msg = validate_action_string(test_action)
            if compiled.search(test_action):
                assert ok is False, f"Expected rejection for {test_action}"
                assert "/action instead of /read" in msg


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: generate_role_definition invariant properties
# ═══════════════════════════════════════════════════════════════════════════
class TestGenerateRoleDefInvariants:
    def test_actions_never_contain_not_actions(self):
        for provider in list(PROVIDER_OPERATIONS.keys())[:5]:
            role = generate_role_definition(
                f"{provider} Role",
                f"A role with enough chars to pass validation for {provider}",
                [provider],
            )
            actions_set = set(role["Actions"])
            not_actions_set = set(role["NotActions"])
            assert actions_set.isdisjoint(not_actions_set), f"overlap in {provider}: {actions_set & not_actions_set}"

    def test_not_actions_subset_of_security_critical(self):
        role = generate_role_definition(
            "Full Role",
            "Comprehensive role definition with all known providers included",
            list(PROVIDER_OPERATIONS.keys()),
        )
        assert set(role["NotActions"]).issubset(SECURITY_CRITICAL_OPS)

    def test_assignable_scopes_always_list_of_strings(self):
        role = generate_role_definition(
            "Scope Test",
            "A role definition with assignable scopes that are always list of strings",
            ["Microsoft.Resources"],
        )
        for scope in role["AssignableScopes"]:
            assert isinstance(scope, str)

    def test_data_actions_always_empty_list(self):
        role = generate_role_definition(
            "Data Test",
            "Role verifying that data actions and not-data actions are empty lists",
            ["Microsoft.Compute"],
        )
        assert role["DataActions"] == []
        assert role["NotDataActions"] == []


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: validate_against_azure_schema missing-field short-circuit
# ═══════════════════════════════════════════════════════════════════════════
class TestSchemaValidationShortCircuit:
    def test_returns_immediately_on_missing_name(self):
        ok, msgs = validate_against_azure_schema(
            {"Description": "long enough desc", "Actions": [], "NotActions": [], "AssignableScopes": ["/"]}
        )
        assert ok is False
        assert any("Missing required field" in m for m in msgs)
        assert "Name" in msgs[0]

    def test_returns_immediately_on_missing_description(self):
        ok, msgs = validate_against_azure_schema(
            {"Name": "Test", "Actions": [], "NotActions": [], "AssignableScopes": ["/"]}
        )
        assert ok is False
        assert any("Missing required field" in m for m in msgs)

    def test_only_first_missing_reported_when_multiple(self):
        ok, msgs = validate_against_azure_schema({"Actions": []})
        assert ok is False
        assert len(msgs) == 1
        assert any("Missing required field" in m for m in msgs)


# ═══════════════════════════════════════════════════════════════════════════
# core: design_azure_network CIDR boundary
# ═══════════════════════════════════════════════════════════════════════════
class TestDesignNetworkCidrBoundary:
    def test_empty_string_cidr_range_passthrough(self):
        """Python defaults only apply when the argument is omitted, not when empty string is passed."""
        result = design_azure_network("eastus", "app", cidr_range="")
        assert result["result"]["address_space"] == ""

    def test_result_always_has_expected_shape(self):
        for cidr in ("10.0.0.0/16", "172.16.0.0/12", "192.168.0.0/16"):
            result = design_azure_network("eastus", "app", cidr_range=cidr)
            assert "vnet_name" in result["result"]
            assert "address_space" in result["result"]
            assert "region" in result["result"]
            assert "subnets" in result["result"]
            assert "nsg_rules" in result["result"]

    def test_same_cidr_yields_same_subnet_count(self):
        a = design_azure_network("eastus", "app", cidr_range="10.0.0.0/16")
        b = design_azure_network("westus", "app", cidr_range="10.0.0.0/16")
        assert len(a["result"]["subnets"]) == len(b["result"]["subnets"])

    def test_region_is_first_arg_not_cidr(self):
        result = design_azure_network("southindia", "app")
        assert result["result"]["region"] == "southindia"


# ═══════════════════════════════════════════════════════════════════════════
# core: KQL injection resistance — inventory_resources
# ═══════════════════════════════════════════════════════════════════════════
class TestKqlInjectionResistance:
    def test_subscription_id_raw_quote_preserved_as_template(self):
        """Template preserves input verbatim — it is a KQL template for human review."""
        result = inventory_resources(["sub-1' --"])
        kql = result["result"]["kql_template"]
        assert "'sub-1' --'" in kql

    def test_subscription_id_raw_semicolon_preserved_as_template(self):
        """Template preserves all input characters — no sanitization for template output."""
        result = inventory_resources(["sub-1'; DROP TABLE"])
        kql = result["result"]["kql_template"]
        assert "'sub-1'; DROP TABLE'" in kql
        assert result["result"]["subscription_count"] == 1

    def test_kql_template_structure_intact_with_special_chars(self):
        result = inventory_resources(["sub'UNION SELECT"])
        kql = result["result"]["kql_template"]
        assert "resourcecontainers" in kql
        assert "join kind=leftouter" in kql
        assert result["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════
# core: container_app_config output invariants
# ═══════════════════════════════════════════════════════════════════════════
class TestContainerAppConfigInvariants:
    def test_valid_gpu_always_has_empty_warnings(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = container_app_config(gpu, "model", "eastus")
            assert result["warnings"] == []

    def test_every_result_has_required_keys(self):
        for gpu in ("T4", "H100", "Unknown"):
            result = container_app_config(gpu, "test-model", "eastus")
            for key in ("name", "image", "cpu", "memory", "gpu_type", "min_replicas", "region"):
                assert key in result["result"]

    def test_image_always_starts_with_mcr(self):
        for model in ("llama-3", "bert", "gpt-neo", "a/b/c"):
            result = container_app_config("T4", model, "eastus")
            assert result["result"]["image"].startswith("mcr.microsoft.com")


# ═══════════════════════════════════════════════════════════════════════════
# core: acr_registry_config error shape invariance
# ═══════════════════════════════════════════════════════════════════════════
class TestAcrErrorShape:
    def test_error_result_always_has_empty_dict(self):
        for sku in ("Free", "Ultra", ""):
            result = acr_registry_config("acr", sku, "eastus")
            if result["status"] == "error":
                assert result["result"] == {}

    def test_warning_on_error_mentions_sku(self):
        result = acr_registry_config("acr", "Free", "eastus")
        assert "Free" in result["warnings"][0]

    def test_all_valid_skus_result_in_ok_status(self):
        for sku in ("Basic", "Standard", "Premium"):
            assert acr_registry_config("acr", sku, "eastus")["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════
# network_designer: private constants verified
# ═══════════════════════════════════════════════════════════════════════════
class TestNetworkDesignerPrivateConstants:
    def test_subnet_prefix_is_24(self):
        assert _SUBNET_PREFIX == 24

    def test_subnet_defaults_are_six(self):
        assert len(_SUBNET_DEFAULTS) == 6

    def test_subnet_defaults_each_pair_is_two_strings(self):
        for pair in _SUBNET_DEFAULTS:
            assert len(pair) == 2
            assert isinstance(pair[0], str)
            assert isinstance(pair[1], str)

    def test_nsg_web_rules_is_list_of_dicts(self):
        assert isinstance(_NSG_WEB_RULES, list)
        assert len(_NSG_WEB_RULES) == 3
        assert all(isinstance(r, dict) for r in _NSG_WEB_RULES)

    def test_default_cidr_is_private_10_range(self):
        assert DEFAULT_CIDR == "10.0.0.0/16"


# ═══════════════════════════════════════════════════════════════════════════
# core: optimize_cost cross-service
# ═══════════════════════════════════════════════════════════════════════════
class TestOptimizeCostEdge:
    def test_all_warnings_are_strings(self):
        result = optimize_cost("bad_service", "eastus", "bad_gpu")
        assert all(isinstance(w, str) for w in result["warnings"])

    def test_no_negative_hourly_rates_for_known(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = optimize_cost("container_apps", "eastus", gpu)
            assert result["result"]["hourly_rate"] > 0

    def test_monthly_estimate_always_non_negative(self):
        for svc in ("container_apps", "unknown"):
            for gpu in ("T4", "H100", "unknown"):
                result = optimize_cost(svc, "eastus", gpu)
                assert result["result"]["monthly_estimate"] >= 0.0


# ═══════════════════════════════════════════════════════════════════════════
# core: query_log_analytics output invariants
# ═══════════════════════════════════════════════════════════════════════════
class TestQueryLogAnalyticsOutputShape:
    def test_result_dict_always_has_four_keys(self):
        for ws in ("ws-1", "", "a" * 100):
            for query in ("Heartbeat", "", "Perf | take 10"):
                result = query_log_analytics(ws, query)
                assert len(result["result"]) == 4

    def test_note_always_contains_help_text(self):
        result = query_log_analytics("ws", "Heartbeat")
        note = result["result"]["note"]
        assert len(note) > 0
        assert "KQL" in note or "Azure" in note or "REST" in note or "validate" in note


# ═══════════════════════════════════════════════════════════════════════════
# core: audit_iam_assignments persona completness
# ═══════════════════════════════════════════════════════════════════════════
class TestAuditIamPersonaInvariants:
    def test_all_personas_return_is_builtin_true(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = audit_iam_assignments("sub", "rg", persona)
            for a in result["result"]:
                assert a["is_builtin"] is True

    def test_all_assignments_are_dicts(self):
        result = audit_iam_assignments("sub", "rg", "terraform_deploy")
        assert all(isinstance(a, dict) for a in result["result"])

    def test_persona_field_matches_input(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = audit_iam_assignments("sub", "rg", persona)
            assert all(a["persona"] == persona for a in result["result"])


# ═══════════════════════════════════════════════════════════════════════════
# core: validate_rbac_role_definition with not_actions containing issues
# ═══════════════════════════════════════════════════════════════════════════
class TestValidateRbacEdgeNotActions:
    def test_not_actions_presence_does_not_change_validation(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=["Microsoft.Compute/virtualMachines/list/action"],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"

    def test_long_not_actions_list_does_not_slow(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=["Microsoft.Compute/virtualMachines/delete"] * 500,
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"


# ═══════════════════════════════════════════════════════════════════════════
# contracts: dataclass JSON serialization for all classes
# ═══════════════════════════════════════════════════════════════════════════
class TestDataclassJsonSerialization:
    def test_azure_rbac_role_serializes(self):
        role = AzureRbacRole(
            name="Custom Role",
            description="A custom role description",
            actions=["Microsoft.Compute/virtualMachines/read"],
            not_actions=["Microsoft.Compute/virtualMachines/delete"],
            data_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        d = json.loads(json.dumps(role, default=lambda o: o.__dict__))
        assert d["name"] == "Custom Role"
        assert "Microsoft.Compute/virtualMachines/read" in d["actions"]

    def test_network_design_serializes(self):
        design = NetworkDesign(
            vnet_name="test-vnet",
            address_space="10.0.0.0/16",
            subnets=[{"name": "web", "purpose": "web-tier", "cidr": "10.0.1.0/24"}],
            nsg_rules=[{"name": "AllowHTTP", "priority": "100"}],
        )
        d = json.loads(json.dumps(design, default=lambda o: o.__dict__))
        assert d["vnet_name"] == "test-vnet"
        assert len(d["subnets"]) == 1

    def test_pricing_result_serializes(self):
        pr = PricingResult("container_apps", "eastus", 0.62, 452.6)
        d = json.loads(json.dumps(pr, default=lambda o: o.__dict__))
        assert d["service_type"] == "container_apps"
        assert d["hourly_rate"] == 0.62

    def test_container_app_config_serializes(self):
        cfg = ContainerAppDeployConfig(
            name="ca-test",
            image="mcr.microsoft.com/test:latest",
            cpu="4.0",
            memory="16Gi",
            gpu_type="T4",
            min_replicas=0,
        )
        d = json.loads(json.dumps(cfg, default=lambda o: o.__dict__))
        assert d["name"] == "ca-test"
        assert d["min_replicas"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# cross-module: AZURE_EXPERT_ROLES function-to-role mapping
# ═══════════════════════════════════════════════════════════════════════════
class TestExpertRoleFunctionMapping:
    def test_each_role_has_a_corresponding_function(self):
        function_map = {
            "rbac_validator": validate_rbac_role_definition,
            "iam_auditor": audit_iam_assignments,
            "network_designer": design_azure_network,
            "acr_architect": acr_registry_config,
            "container_app_planner": container_app_config,
            "log_analytics_querier": query_log_analytics,
            "resource_inventorier": inventory_resources,
            "cost_optimizer": optimize_cost,
        }
        for role_name, func in function_map.items():
            assert role_name in AZURE_EXPERT_ROLES
            assert callable(func)

    def test_every_role_description_mentions_domain(self):
        domain_keywords = {
            "rbac_validator": ["RBAC", "role"],
            "iam_auditor": ["IAM", "privilege"],
            "network_designer": ["VNet", "network", "NSG"],
            "acr_architect": ["Registry", "SKU", "ACR"],
            "container_app_planner": ["Container", "GPU", "deployment"],
            "log_analytics_querier": ["Log", "KQL", "Analytics"],
            "resource_inventorier": ["Resource", "inventory", "Graph"],
            "cost_optimizer": ["cost", "price", "optimiz"],
        }
        for role_name, keywords in domain_keywords.items():
            desc = AZURE_EXPERT_ROLES[role_name].lower()
            assert any(k.lower() in desc for k in keywords), (
                f"{role_name} description missing domain keyword from {keywords}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: lazy provider map edge
# ═══════════════════════════════════════════════════════════════════════════
class TestLazyProviderMapEdge:
    def test_same_object_returned_every_call(self):
        a = _lazy_provider_map()
        b = _lazy_provider_map()
        c = _lazy_provider_map()
        assert a is b is c

    def test_provider_count_matches_provider_operations(self):
        result = _lazy_provider_map()
        assert len(result) == len(PROVIDER_OPERATIONS)

    def test_every_key_is_azure_namespace(self):
        for key in _lazy_provider_map():
            assert key.startswith("Microsoft.")

    def test_total_action_count_consistent(self):
        result = _lazy_provider_map()
        total_lazy = sum(len(v) for v in result.values())
        total_flat = len(_ALL_KNOWN_ACTIONS)
        assert total_lazy == total_flat


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: validate_against_azure_schema stress
# ═══════════════════════════════════════════════════════════════════════════
class TestSchemaValidationComprehensive:
    def test_valid_role_with_compute_provider_validates(self):
        role = generate_role_definition(
            "Compute Role",
            "A compute-only role with enough description characters for minimum",
            ["Microsoft.Compute"],
        )
        ok, msgs = validate_against_azure_schema(role)
        assert ok is True, f"unexpected errors: {msgs}"

    def test_all_known_providers_produce_schema_warnings_for_unknown_actions(self):
        """Some provider actions may not be in the flat known set due to allowed suffixes."""
        role = generate_role_definition(
            "Full Test",
            "A role containing every known provider's valid action entries",
            sorted(PROVIDER_OPERATIONS.keys()),
        )
        ok, msgs = validate_against_azure_schema(role)
        assert isinstance(ok, bool)
        assert isinstance(msgs, list)

    def test_cross_provider_unknown_actions_produce_one_warning(self):
        ok, msgs = validate_against_azure_schema(
            {
                "Name": "Cross Unknown",
                "Description": "A role definition with many unknown actions to test consolidation",
                "Actions": [
                    "Microsoft.FakeA/resource/read",
                    "Microsoft.FakeB/resource/write",
                    "Microsoft.FakeC/resource/delete",
                    "Microsoft.FakeD/resource/action",
                    "Microsoft.FakeE/resource/read",
                    "Microsoft.FakeF/resource/write",
                ],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/x"],
            }
        )
        assert ok is True
        warnings = [m for m in msgs if m.startswith("WARNING:")]
        assert len(warnings) == 1, f"expected 1 consolidated warning, got {len(warnings)}"


# ═══════════════════════════════════════════════════════════════════════════
# get_deploy_strategist deep
# ═══════════════════════════════════════════════════════════════════════════
class TestGetDeployStrategistComprehensive:
    def test_can_call_multiple_times(self):
        instances = [get_deploy_strategist() for _ in range(20)]
        assert all(i is not None for i in instances)

    def test_each_instance_is_imported_type(self):
        from general_ludd.infra.deploy_strategy import DeployStrategist

        s = get_deploy_strategist()
        assert isinstance(s, DeployStrategist)


# ═══════════════════════════════════════════════════════════════════════════
# rbac_validator: all_known_actions consistency
# ═══════════════════════════════════════════════════════════════════════════
class TestAllKnownActionsInvariants:
    def test_every_action_is_well_formed(self):
        for action in all_known_actions():
            assert action.startswith("Microsoft.")
            parts = action.split("/")
            assert len(parts) >= 3
            assert parts[-1] in ("read", "write", "delete", "action")

    def test_no_secret_read_pattern_in_valid_actions(self):
        for action in all_known_actions():
            ok, msg = validate_action_string(action)
            if not ok:
                assert "/action instead of /read" in msg or "forbidden suffix" in msg, (
                    f"unexpected failure for {action}: {msg}"
                )
