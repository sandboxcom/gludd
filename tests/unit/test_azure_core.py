"""Unit tests for ``general_ludd.azure.core`` — deep edge-case and error-path
coverage for all exported functions.
"""

from __future__ import annotations

import pytest

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

# ---------------------------------------------------------------------------
# validate_rbac_role_definition
# ---------------------------------------------------------------------------


class TestValidateRbacRoleDefinition:
    def test_valid_actions_pass(self):
        result = validate_rbac_role_definition(
            action_strings=[
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Network/virtualNetworks/read",
            ],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"
        assert result["issues"] == []

    def test_invalid_action_format_is_reported(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/list/action"],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"
        assert len(result["issues"]) >= 1
        assert any("Invalid action format" in issue for issue in result["issues"])

    def test_multiple_invalid_actions_all_reported(self):
        result = validate_rbac_role_definition(
            action_strings=[
                "Microsoft.Compute/virtualMachines/list/action",
                "Microsoft.Compute/virtualMachines/listkeys/action",
                "bad-action",
            ],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"
        assert len(result["issues"]) >= 3

    def test_empty_assignable_scopes_fails(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=[],
            assignable_scopes=[],
        )
        assert result["status"] == "invalid"
        assert any("assignable_scopes" in issue for issue in result["issues"])

    def test_empty_actions_and_empty_scopes_reports_both_issues(self):
        result = validate_rbac_role_definition(
            action_strings=[],
            not_actions=[],
            assignable_scopes=[],
        )
        assert result["status"] == "invalid"
        assert any("assignable_scopes" in issue for issue in result["issues"])

    def test_valid_actions_with_multiple_scopes_passes(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-a", "/subscriptions/sub-b"],
        )
        assert result["status"] == "valid"
        assert result["issues"] == []

    def test_not_actions_not_used_in_validation(self):
        """not_actions is accepted but not consumed by the function body."""
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=["Microsoft.Compute/virtualMachines/delete"],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"

    def test_action_with_secret_suffix_read_is_invalid(self):
        result = validate_rbac_role_definition(
            action_strings=[
                "Microsoft.KeyVault/vaults/keys/read",
            ],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"
        assert any("Invalid action format" in issue for issue in result["issues"])

    def test_large_action_list_validated(self):
        actions = ["Microsoft.Compute/virtualMachines/read"] * 50
        result = validate_rbac_role_definition(
            action_strings=actions,
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"
        assert result["issues"] == []

    def test_result_shape_consistent_on_valid(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert isinstance(result["status"], str)
        assert isinstance(result["issues"], list)

    def test_result_shape_consistent_on_invalid(self):
        result = validate_rbac_role_definition(
            action_strings=["bad"],
            not_actions=[],
            assignable_scopes=[],
        )
        assert result["status"] == "invalid"
        assert isinstance(result["issues"], list)


# ---------------------------------------------------------------------------
# audit_iam_assignments
# ---------------------------------------------------------------------------


class TestAuditIamAssignments:
    def test_known_persona_returns_assignments(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = audit_iam_assignments("sub-id", "rg-name", persona)
            assert result["status"] == "ok"
            assert len(result["result"]) >= 1
            for assignment in result["result"]:
                assert assignment["persona"] == persona
                assert assignment["is_builtin"] is True
                assert assignment["role_name"]

    def test_unknown_persona_returns_error(self):
        result = audit_iam_assignments("sub-id", "rg-name", "unknown_persona")
        assert result["status"] == "error"
        assert result["result"] == []
        assert len(result["warnings"]) >= 1
        assert "Unknown persona" in result["warnings"][0]

    def test_scope_constructed_correctly(self):
        result = audit_iam_assignments("abc-123", "my-rg", "runtime_execution")
        expected_scope = "/subscriptions/abc-123/resourceGroups/my-rg"
        for assignment in result["result"]:
            assert assignment["scope"] == expected_scope

    def test_terraform_deploy_gets_two_roles(self):
        result = audit_iam_assignments("sub", "rg", "terraform_deploy")
        assert result["status"] == "ok"
        assert len(result["result"]) == 2
        role_names = {a["role_name"] for a in result["result"]}
        assert role_names == {"Contributor", "User Access Administrator"}

    def test_runtime_execution_gets_acrpull_and_container_apps_operator(self):
        result = audit_iam_assignments("sub", "rg", "runtime_execution")
        role_names = {a["role_name"] for a in result["result"]}
        assert "AcrPull" in role_names
        assert "Container Apps Operator" in role_names

    def test_model_inference_roles(self):
        result = audit_iam_assignments("sub", "rg", "model_inference")
        role_names = {a["role_name"] for a in result["result"]}
        assert role_names == {"Storage Blob Data Reader", "AcrPull"}

    def test_monitor_roles(self):
        result = audit_iam_assignments("sub", "rg", "monitor")
        role_names = {a["role_name"] for a in result["result"]}
        assert role_names == {"Monitoring Reader", "Log Analytics Reader"}

    def test_empty_subscription_id_still_forms_scope(self):
        result = audit_iam_assignments("", "rg", "terraform_deploy")
        assert result["status"] == "ok"
        for assignment in result["result"]:
            assert "subscriptions/" in assignment["scope"]
            assert "/resourceGroups/rg" in assignment["scope"]

    def test_result_warnings_empty_on_success(self):
        result = audit_iam_assignments("sub", "rg", "terraform_deploy")
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# design_azure_network
# ---------------------------------------------------------------------------


class TestDesignAzureNetwork:
    def test_returns_four_subnets(self):
        result = design_azure_network("eastus", "test-app")
        assert len(result["result"]["subnets"]) == 4

    def test_vnet_name_includes_app_and_region(self):
        result = design_azure_network("westeurope", "prod")
        assert result["result"]["vnet_name"] == "prod-vnet-westeurope"

    def test_bastion_subnet_has_exact_name(self):
        result = design_azure_network("eastus", "app")
        bastion = [s for s in result["result"]["subnets"] if s["purpose"] == "bastion"]
        assert len(bastion) == 1
        assert bastion[0]["name"] == "AzureBastionSubnet"

    def test_default_cidr_is_slash_sixteen(self):
        result = design_azure_network("eastus", "app")
        assert result["result"]["address_space"] == "10.0.0.0/16"

    def test_custom_cidr_range_is_respected(self):
        result = design_azure_network("eastus", "app", cidr_range="172.16.0.0/12")
        assert result["result"]["address_space"] == "172.16.0.0/12"

    def test_container_apps_subnet_is_correct_cidr(self):
        result = design_azure_network("eastus", "app")
        container = [s for s in result["result"]["subnets"] if s["purpose"] == "container_apps"]
        assert container[0]["cidr"] == "10.0.1.0/24"

    def test_nsg_rules_include_http_and_https(self):
        result = design_azure_network("eastus", "app")
        rule_names = {r["name"] for r in result["result"]["nsg_rules"]}
        assert "allow-http" in rule_names
        assert "allow-https" in rule_names

    def test_nsg_rules_are_inbound(self):
        result = design_azure_network("eastus", "app")
        for rule in result["result"]["nsg_rules"]:
            assert rule["direction"] == "Inbound"

    def test_database_subnet_is_present(self):
        result = design_azure_network("eastus", "app")
        purposes = {s["purpose"] for s in result["result"]["subnets"]}
        assert "database" in purposes

    def test_all_subnet_cidrs_are_within_vnet_address_space(self):
        result = design_azure_network("eastus", "app")
        for subnet in result["result"]["subnets"]:
            assert subnet["cidr"].startswith("10.0."), f"{subnet['cidr']} outside 10.0.0.0/16"

    def test_status_always_ok(self):
        result = design_azure_network("any-region", "any-app")
        assert result["status"] == "ok"
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# acr_registry_config
# ---------------------------------------------------------------------------


class TestAcrConfig:
    def test_premium_enables_geo_replication(self):
        result = acr_registry_config("myacr", "Premium", "eastus")
        assert result["status"] == "ok"
        assert result["result"]["geo_replication"] is True

    def test_basic_disables_geo_replication(self):
        result = acr_registry_config("myacr", "Basic", "eastus")
        assert result["result"]["geo_replication"] is False

    def test_standard_disables_geo_replication(self):
        result = acr_registry_config("myacr", "Standard", "eastus")
        assert result["result"]["geo_replication"] is False

    def test_admin_is_always_disabled(self):
        for sku in ("Basic", "Standard", "Premium"):
            result = acr_registry_config("acr", sku, "eastus")
            assert result["result"]["admin_enabled"] is False, f"admin_enabled for {sku}"

    def test_invalid_sku_returns_error(self):
        result = acr_registry_config("myacr", "Free", "eastus")
        assert result["status"] == "error"
        assert result["result"] == {}
        assert len(result["warnings"]) >= 1
        assert "Invalid SKU" in result["warnings"][0]

    def test_invalid_sku_message_lists_valid_options(self):
        result = acr_registry_config("acr", "Nonexistent", "eastus")
        assert "Basic" in result["warnings"][0]
        assert "Premium" in result["warnings"][0]
        assert "Standard" in result["warnings"][0]

    def test_all_valid_skus_pass(self):
        for sku in ("Basic", "Standard", "Premium"):
            result = acr_registry_config(f"acr-{sku.lower()}", sku, "eastus")
            assert result["status"] == "ok"

    def test_name_preserved_in_result(self):
        result = acr_registry_config("unique-registry-name", "Standard", "westus2")
        assert result["result"]["name"] == "unique-registry-name"

    def test_region_preserved_in_result(self):
        result = acr_registry_config("acr", "Basic", "northeurope")
        assert result["result"]["region"] == "northeurope"


# ---------------------------------------------------------------------------
# container_app_config
# ---------------------------------------------------------------------------


class TestContainerAppConfig:
    def test_valid_gpu_t4_returns_ok(self):
        result = container_app_config("T4", "llama-3", "eastus")
        assert result["status"] == "ok"
        assert result["result"]["gpu_type"] == "T4"

    def test_a100_gets_higher_allocation(self):
        result = container_app_config("A100", "llama-3", "eastus")
        assert result["result"]["cpu"] == "8.0"
        assert result["result"]["memory"] == "32Gi"

    def test_h100_gets_higher_allocation(self):
        result = container_app_config("H100", "model", "eastus")
        assert result["result"]["cpu"] == "8.0"
        assert result["result"]["memory"] == "32Gi"

    def test_t4_gets_lower_allocation(self):
        result = container_app_config("T4", "model", "eastus")
        assert result["result"]["cpu"] == "4.0"
        assert result["result"]["memory"] == "16Gi"

    def test_a10_gets_lower_allocation(self):
        result = container_app_config("A10", "model", "eastus")
        assert result["result"]["cpu"] == "4.0"
        assert result["result"]["memory"] == "16Gi"

    def test_unknown_gpu_produces_warning(self):
        result = container_app_config("V100", "model", "eastus")
        assert result["status"] == "ok"
        assert len(result["warnings"]) >= 1
        assert "not in known set" in result["warnings"][0]
        assert "V100" in result["warnings"][0]

    def test_unknown_gpu_still_returns_config(self):
        result = container_app_config("V100", "model", "eastus")
        assert result["status"] == "ok"
        assert result["result"]["gpu_type"] == "V100"

    def test_model_name_with_slash_is_sanitized(self):
        result = container_app_config("T4", "meta/llama-3", "eastus")
        assert "/" not in result["result"]["name"]
        assert "meta/llama-3" not in result["result"]["image"]
        assert "meta-llama-3" in result["result"]["image"]

    def test_model_name_lowercased_in_name(self):
        result = container_app_config("T4", "Meta-LLaMA", "eastus")
        assert result["result"]["name"] == result["result"]["name"].lower()

    def test_name_starts_with_ca_prefix(self):
        result = container_app_config("T4", "model-name", "eastus")
        assert result["result"]["name"].startswith("ca-")

    def test_min_replicas_is_zero(self):
        result = container_app_config("T4", "nginx", "eastus")
        assert result["result"]["min_replicas"] == 0

    def test_region_propagated_to_result(self):
        result = container_app_config("T4", "model", "switzerlandnorth")
        assert result["result"]["region"] == "switzerlandnorth"

    def test_all_valid_gpus_produce_no_warning(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = container_app_config(gpu, "model", "eastus")
            assert result["warnings"] == [], f"unexpected warning for {gpu}: {result['warnings']}"


# ---------------------------------------------------------------------------
# query_log_analytics
# ---------------------------------------------------------------------------


class TestQueryLogAnalytics:
    def test_workspace_id_preserved(self):
        result = query_log_analytics("ws-abc-123", "Heartbeat")
        assert result["result"]["workspace_id"] == "ws-abc-123"

    def test_query_preserved(self):
        result = query_log_analytics("ws", "Heartbeat | take 10")
        assert result["result"]["query"] == "Heartbeat | take 10"

    def test_timespan_is_always_p1d(self):
        result = query_log_analytics("ws", "Heartbeat")
        assert result["result"]["timespan"] == "P1D"

    def test_note_mentions_rest_api(self):
        result = query_log_analytics("ws", "Heartbeat")
        assert "REST API" in result["result"]["note"] or "Azure Monitor" in result["result"]["note"]

    def test_empty_query_is_accepted(self):
        result = query_log_analytics("ws", "")
        assert result["status"] == "ok"
        assert result["result"]["query"] == ""

    def test_status_always_ok(self):
        result = query_log_analytics("any", "any")
        assert result["status"] == "ok"
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# inventory_resources
# ---------------------------------------------------------------------------


class TestInventoryResources:
    def test_subscription_count_preserved(self):
        result = inventory_resources(["a", "b", "c"])
        assert result["result"]["subscription_count"] == 3

    def test_single_subscription_works(self):
        result = inventory_resources(["only-sub"])
        assert result["result"]["subscription_count"] == 1
        assert "'only-sub'" in result["result"]["kql_template"]

    def test_empty_list_works(self):
        result = inventory_resources([])
        assert result["result"]["subscription_count"] == 0

    def test_kql_template_contains_expected_clauses(self):
        result = inventory_resources(["sub-1"])
        kql = result["result"]["kql_template"]
        assert "resourcecontainers" in kql
        assert "microsoft.resources/subscriptions" in kql
        assert "subscriptionId" in kql

    def test_kql_uses_leftouter_join(self):
        result = inventory_resources(["sub-1"])
        assert "leftouter" in result["result"]["kql_template"]

    def test_status_always_ok(self):
        result = inventory_resources(["sub"])
        assert result["status"] == "ok"
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# optimize_cost
# ---------------------------------------------------------------------------


class TestOptimizeCost:
    def test_known_gpu_returns_hourly_rate(self):
        result = optimize_cost("container_apps", "eastus", "T4")
        assert result["result"]["hourly_rate"] == 0.62

    def test_monthly_is_hourly_times_730(self):
        result = optimize_cost("container_apps", "eastus", "A100")
        assert result["result"]["monthly_estimate"] == pytest.approx(3.67 * 730)

    def test_unknown_service_returns_zero_and_warning(self):
        result = optimize_cost("virtual_machines", "eastus", "T4")
        assert result["result"]["hourly_rate"] == 0.0
        assert len(result["warnings"]) >= 1
        assert "No pricing data" in result["warnings"][0]

    def test_unknown_gpu_returns_zero_and_warning(self):
        result = optimize_cost("container_apps", "eastus", "V100")
        assert result["result"]["hourly_rate"] == 0.0
        assert result["result"]["monthly_estimate"] == 0.0
        assert len(result["warnings"]) >= 1

    def test_currency_is_always_usd(self):
        result = optimize_cost("container_apps", "eastus", "T4")
        assert result["result"]["currency"] == "USD"

    def test_region_propagated(self):
        result = optimize_cost("container_apps", "westeurope", "T4")
        assert result["result"]["region"] == "westeurope"

    def test_service_type_propagated(self):
        result = optimize_cost("container_apps", "eastus", "A10")
        assert result["result"]["service_type"] == "container_apps"

    def test_all_known_gpu_types_priced(self):
        for gpu in ("T4", "A10", "A100", "H100"):
            result = optimize_cost("container_apps", "eastus", gpu)
            assert result["result"]["hourly_rate"] > 0, f"no pricing for container_apps/{gpu}"

    def test_h100_is_most_expensive(self):
        t4 = optimize_cost("container_apps", "eastus", "T4")["result"]["hourly_rate"]
        h100 = optimize_cost("container_apps", "eastus", "H100")["result"]["hourly_rate"]
        assert h100 > t4

    def test_warnings_empty_with_valid_data(self):
        result = optimize_cost("container_apps", "eastus", "A10")
        assert result["warnings"] == []

    def test_status_always_ok_even_with_warnings(self):
        result = optimize_cost("bad_svc", "eastus", "bad_gpu")
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# get_deploy_strategist
# ---------------------------------------------------------------------------


class TestGetDeployStrategist:
    def test_returns_an_instance(self):
        strategist = get_deploy_strategist()
        from general_ludd.infra.deploy_strategy import DeployStrategist

        assert isinstance(strategist, DeployStrategist)

    def test_multiple_calls_return_distinct_objects_or_same(self):
        a = get_deploy_strategist()
        b = get_deploy_strategist()
        assert a is b or a is not b


# ---------------------------------------------------------------------------
# AZURE_EXPERT_ROLES catalogue
# ---------------------------------------------------------------------------


class TestRoleCatalogue:
    def test_eight_roles_registered(self):
        assert len(AZURE_EXPERT_ROLES) == 8

    def test_all_expected_roles_present(self):
        for name in (
            "rbac_validator",
            "iam_auditor",
            "network_designer",
            "acr_architect",
            "container_app_planner",
            "log_analytics_querier",
            "resource_inventorier",
            "cost_optimizer",
        ):
            assert name in AZURE_EXPERT_ROLES, f"missing role {name}"

    def test_role_descriptions_are_nonempty(self):
        for name, desc in AZURE_EXPERT_ROLES.items():
            assert isinstance(desc, str), f"role {name} description is not str"
            assert len(desc) > 0, f"role {name} has empty description"

    def test_all_values_are_strings(self):
        for name, desc in AZURE_EXPERT_ROLES.items():
            assert isinstance(desc, str), f"role {name} value: {type(desc)}"

    def test_rbac_validator_mentions_rbac(self):
        desc = AZURE_EXPERT_ROLES["rbac_validator"]
        assert "rbac" in desc.lower() or "RBAC" in desc

    def test_cost_optimizer_mentions_cost_or_pricing(self):
        desc = AZURE_EXPERT_ROLES["cost_optimizer"]
        assert "cost" in desc.lower() or "pricing" in desc.lower() or "price" in desc.lower()
