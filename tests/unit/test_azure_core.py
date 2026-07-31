"""Unit tests for ``general_ludd.azure.core`` — role catalogue, RBAC validation,
IAM audit, and domain entry-points.
"""

from __future__ import annotations

from general_ludd.azure.core import (
    AZURE_EXPERT_ROLES,
    acr_registry_config,
    audit_iam_assignments,
    container_app_config,
    design_azure_network,
    inventory_resources,
    optimize_cost,
    query_log_analytics,
    validate_rbac_role_definition,
)


class TestRoleCatalogue:
    def test_eight_roles_registered(self):
        assert len(AZURE_EXPERT_ROLES) == 8
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
            assert desc, f"role {name} has empty description"


class TestValidateRbacRoleDefinition:
    def test_valid_actions_pass(self):
        result = validate_rbac_role_definition(
            action_strings=[
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Network/virtualNetworks/read",
            ],
            not_actions=[
                "Microsoft.Compute/virtualMachines/runCommand/action",
                "Microsoft.Compute/disks/delete",
                "Microsoft.Network/networkSecurityGroups/delete",
                "Microsoft.Network/routeTables/delete",
                "Microsoft.Authorization/roleAssignments/write",
                "Microsoft.Authorization/roleDefinitions/write",
                "Microsoft.KeyVault/vaults/delete",
                "Microsoft.Storage/storageAccounts/delete",
            ],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"
        assert result["issues"] == []

    def test_invalid_action_suffix_fails(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/list/action"],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "invalid"
        assert len(result["issues"]) >= 1

    def test_missing_not_actions_for_security_critical(self):
        result = validate_rbac_role_definition(
            action_strings=["Microsoft.Compute/virtualMachines/read"],
            not_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"
        assert result["issues"] == []

    def test_empty_actions_fails(self):
        result = validate_rbac_role_definition(
            action_strings=[],
            not_actions=[],
            assignable_scopes=[],
        )
        assert result["status"] == "invalid"
        assert len(result["issues"]) >= 1

    def test_valid_complete_role_returns_empty_issues(self):
        result = validate_rbac_role_definition(
            action_strings=[
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Network/virtualNetworks/read",
            ],
            not_actions=[
                "Microsoft.Compute/virtualMachines/runCommand/action",
                "Microsoft.Compute/disks/delete",
                "Microsoft.Network/networkSecurityGroups/delete",
                "Microsoft.Network/routeTables/delete",
                "Microsoft.Authorization/roleAssignments/write",
                "Microsoft.Authorization/roleDefinitions/write",
                "Microsoft.KeyVault/vaults/delete",
                "Microsoft.Storage/storageAccounts/delete",
            ],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert result["status"] == "valid"
        assert result["issues"] == []


class TestAuditIamAssignments:
    def test_returns_correct_roles_for_each_persona(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = audit_iam_assignments(
                subscription_id="sub-id",
                resource_group="rg",
                persona=persona,
            )
            assert result["status"] == "ok"
            assert len(result["result"]) >= 1

    def test_each_role_function_returns_typed_dict(self):
        result = audit_iam_assignments(
            subscription_id="sub-id",
            resource_group="rg",
            persona="terraform_deploy",
        )
        assert "status" in result
        assert "result" in result
        assert "warnings" in result
        assert isinstance(result["warnings"], list)
        assert isinstance(result["result"], list)


class TestAcrConfig:
    def test_returns_typed_result(self):
        r = acr_registry_config(name="myacr", sku="Standard", region="eastus")
        assert r["status"] == "ok"
        assert r["result"]["name"] == "myacr"
        assert r["result"]["sku"] == "Standard"
        assert isinstance(r["warnings"], list)


class TestContainerAppConfig:
    def test_returns_typed_result(self):
        r = container_app_config(gpu_type="T4", model_name="nginx", region="eastus")
        assert r["status"] == "ok"
        assert "gpu_type" in r["result"]
        assert r["result"]["gpu_type"] == "T4"
        assert isinstance(r["warnings"], list)


class TestQueryLogAnalytics:
    def test_returns_typed_result(self):
        r = query_log_analytics("ws-id", "Heartbeat")
        assert r["status"] == "ok"
        assert r["result"]["workspace_id"] == "ws-id"
        assert isinstance(r["warnings"], list)


class TestInventoryResources:
    def test_returns_typed_result(self):
        r = inventory_resources(["sub-1", "sub-2"])
        assert r["status"] == "ok"
        assert r["result"]["subscription_count"] == 2
        assert isinstance(r["warnings"], list)


class TestOptimizeCost:
    def test_returns_typed_result(self):
        r = optimize_cost(service_type="container_apps", region="eastus", gpu_type="T4")
        assert r["status"] == "ok"
        assert r["result"]["hourly_rate"] > 0
        assert isinstance(r["warnings"], list)


class TestDesignAzureNetwork:
    def test_returns_network_design(self):
        design = design_azure_network(region="eastus", app_name="test-app")
        assert design["status"] == "ok"
        assert design["result"]["vnet_name"] == "test-app-vnet-eastus"
        assert len(design["result"]["subnets"]) >= 2
        assert len(design["result"]["nsg_rules"]) >= 1
