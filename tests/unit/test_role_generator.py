"""Tests for src/general_ludd/cloud/role_generator.py — template-based cloud role generator."""

from __future__ import annotations

from typing import ClassVar

import pytest

from general_ludd.cloud.role_generator import (
    ROLE_TEMPLATES,
    __all__,
    _aws_action_matches,
    _azure_action_matches,
    _prune_by_resource_types,
    generate_role_from_template,
)


class TestRoleTemplatesStructure:
    """Verify ROLE_TEMPLATES has the expected shape for all providers and personas."""

    PROVIDERS: ClassVar[list[str]] = ["azure", "aws", "gcp"]
    PERSONAS: ClassVar[list[str]] = ["terraform_deploy", "runtime_execution", "model_inference", "monitor"]

    def test_all_providers_present(self):
        for provider in self.PROVIDERS:
            assert provider in ROLE_TEMPLATES, f"Missing provider: {provider}"

    def test_all_personas_present(self):
        for provider in self.PROVIDERS:
            for persona in self.PERSONAS:
                assert persona in ROLE_TEMPLATES[provider], f"Missing persona {persona!r} for provider {provider!r}"

    def test_azure_templates_have_required_fields(self):
        required = {"Name", "Description", "Actions", "NotActions", "AssignableScopes", "DataActions", "NotDataActions"}
        for persona, template in ROLE_TEMPLATES["azure"].items():
            missing = required - set(template.keys())
            assert not missing, f"Azure/{persona} missing fields: {missing}"

    def test_aws_templates_have_required_fields(self):
        for persona, template in ROLE_TEMPLATES["aws"].items():
            assert "role_name" in template, f"AWS/{persona} missing role_name"
            assert "description" in template, f"AWS/{persona} missing description"
            assert "policy" in template, f"AWS/{persona} missing policy"
            assert isinstance(template["policy"], list), f"AWS/{persona} policy not a list"
            assert len(template["policy"]) > 0, f"AWS/{persona} policy is empty"
            for stmt in template["policy"]:
                assert "Effect" in stmt, f"AWS/{persona} statement missing Effect"
                assert "Action" in stmt, f"AWS/{persona} statement missing Action"
                assert "Resource" in stmt, f"AWS/{persona} statement missing Resource"

    def test_gcp_templates_have_required_fields(self):
        for persona, template in ROLE_TEMPLATES["gcp"].items():
            assert "role_name" in template, f"GCP/{persona} missing role_name"
            assert "description" in template, f"GCP/{persona} missing description"
            assert "bindings" in template, f"GCP/{persona} missing bindings"
            assert isinstance(template["bindings"], list), f"GCP/{persona} bindings not a list"
            assert len(template["bindings"]) > 0, f"GCP/{persona} bindings is empty"


class TestGenerateRoleFromTemplateSuccess:
    """Happy-path role generation for all provider x persona combinations."""

    def test_azure_terraform_deploy(self):
        result = generate_role_from_template("azure", "terraform_deploy")
        assert result["status"] == "ok"
        assert result["role_definition"]["Name"] == "custom-terraform-deploy"
        assert "Microsoft.Compute/*" in result["role_definition"]["Actions"]
        assert len(result["warnings"]) == 0

    def test_azure_runtime_execution(self):
        result = generate_role_from_template("azure", "runtime_execution")
        assert result["status"] == "ok"
        assert result["role_definition"]["Name"] == "custom-runtime-execution"
        assert "Microsoft.OperationalInsights/workspaces/query/action" in result["role_definition"]["Actions"]

    def test_azure_model_inference(self):
        result = generate_role_from_template("azure", "model_inference")
        assert result["status"] == "ok"
        assert result["role_definition"]["Name"] == "custom-model-inference"

    def test_azure_monitor(self):
        result = generate_role_from_template("azure", "monitor")
        assert result["status"] == "ok"
        assert result["role_definition"]["Name"] == "custom-monitor"
        assert "Microsoft.Insights/metrics/read" in result["role_definition"]["Actions"]

    def test_aws_terraform_deploy(self):
        result = generate_role_from_template("aws", "terraform_deploy")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "terraform-deploy"
        assert len(result["role_definition"]["policy"]) == 2
        assert result["role_definition"]["policy"][0]["Effect"] == "Allow"

    def test_aws_runtime_execution(self):
        result = generate_role_from_template("aws", "runtime_execution")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "runtime-execution"

    def test_aws_model_inference(self):
        result = generate_role_from_template("aws", "model_inference")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "model-inference"

    def test_aws_monitor(self):
        result = generate_role_from_template("aws", "monitor")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "monitor"
        assert "cloudwatch:GetMetricData" in result["role_definition"]["policy"][0]["Action"]

    def test_gcp_terraform_deploy(self):
        result = generate_role_from_template("gcp", "terraform_deploy")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "terraform-deploy"
        assert len(result["role_definition"]["bindings"]) == 3

    def test_gcp_runtime_execution(self):
        result = generate_role_from_template("gcp", "runtime_execution")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "runtime-execution"

    def test_gcp_model_inference(self):
        result = generate_role_from_template("gcp", "model_inference")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "model-inference"

    def test_gcp_monitor(self):
        result = generate_role_from_template("gcp", "monitor")
        assert result["status"] == "ok"
        assert result["role_definition"]["role_name"] == "monitor"

    def test_generated_role_is_top_level_copy(self):
        """Top-level dict is a copy; nested lists share the template reference."""
        r1 = generate_role_from_template("azure", "monitor")
        r2 = generate_role_from_template("azure", "monitor")
        assert r1 is not r2
        assert r1["role_definition"] is not r2["role_definition"]


class TestGenerateRoleFromTemplateErrors:
    """Error case tests for generate_role_from_template."""

    def test_unknown_provider(self):
        result = generate_role_from_template("alibaba", "monitor")
        assert result["status"] == "error"
        assert result["role_definition"] == {}
        assert len(result["warnings"]) == 1
        assert "Unknown provider" in result["warnings"][0]
        assert "alibaba" in result["warnings"][0]
        assert "azure" in result["warnings"][0]
        assert "aws" in result["warnings"][0]
        assert "gcp" in result["warnings"][0]

    def test_unknown_persona_azure(self):
        result = generate_role_from_template("azure", "super_admin")
        assert result["status"] == "error"
        assert result["role_definition"] == {}
        assert len(result["warnings"]) == 1
        assert "Unknown persona" in result["warnings"][0]
        assert "super_admin" in result["warnings"][0]

    def test_unknown_persona_aws(self):
        result = generate_role_from_template("aws", "nuclear_launch")
        assert result["status"] == "error"
        assert "Unknown persona" in result["warnings"][0]
        assert "nuclear_launch" in result["warnings"][0]

    def test_unknown_persona_gcp(self):
        result = generate_role_from_template("gcp", "root")
        assert result["status"] == "error"
        assert "Unknown persona" in result["warnings"][0]
        assert "root" in result["warnings"][0]

    def test_unknown_persona_warning_lists_known(self):
        result = generate_role_from_template("azure", "bogus")
        warning = result["warnings"][0]
        for known in ROLE_TEMPLATES["azure"]:
            assert known in warning

    @pytest.mark.parametrize("provider", ["", "AWS", "Azure", "Aws", None])
    def test_invalid_provider_strings(self, provider):
        result = generate_role_from_template(provider, "monitor")
        assert result["status"] == "error"


class TestResourceTypePruning:
    """Tests for resource-type-based action pruning."""

    def test_empty_resource_types_no_pruning(self):
        result = generate_role_from_template("azure", "monitor", [])
        assert result["status"] == "ok"
        assert len(result["role_definition"]["Actions"]) >= 1
        assert len(result["warnings"]) == 0

    def test_none_resource_types_no_pruning(self):
        result = generate_role_from_template("aws", "monitor", None)
        assert result["status"] == "ok"
        assert len(result["role_definition"]["policy"][0]["Action"]) > 0
        assert len(result["warnings"]) == 0

    def test_azure_pruning_compute_only(self):
        result = generate_role_from_template("azure", "terraform_deploy", ["compute"])
        actions = result["role_definition"]["Actions"]
        assert len(actions) > 0
        for a in actions:
            assert _azure_action_matches(a, {"compute"}), f"Action {a!r} does not match compute"

    def test_azure_pruning_wildcard_resource(self):
        result = generate_role_from_template("azure", "terraform_deploy", ["*"])
        actions = result["role_definition"]["Actions"]
        assert len(actions) == len(ROLE_TEMPLATES["azure"]["terraform_deploy"]["Actions"])

    def test_azure_pruning_no_match_removes_all(self):
        result = generate_role_from_template("azure", "model_inference", ["bogus_resource"])
        assert result["role_definition"]["Actions"] == []
        assert len(result["warnings"]) == 1
        assert "Pruned" in result["warnings"][0]

    def test_aws_pruning_ec2_only(self):
        result = generate_role_from_template("aws", "terraform_deploy", ["ec2"])
        for stmt in result["role_definition"]["policy"]:
            for action in stmt["Action"]:
                assert "ec2" in action.lower() or "iam" in action.lower()

    def test_aws_pruning_s3_only(self):
        result = generate_role_from_template("aws", "model_inference", ["s3"])
        actions_flat = []
        for stmt in result["role_definition"]["policy"]:
            actions_flat.extend(stmt["Action"])
        assert all("s3" in a.lower() for a in actions_flat)

    def test_aws_pruning_wildcard_keeps_all(self):
        result = generate_role_from_template("aws", "terraform_deploy", ["*"])
        original_count = sum(
            len(s["Action"])
            for s in ROLE_TEMPLATES["aws"]["terraform_deploy"]["policy"]
            if isinstance(s["Action"], list)
        )
        pruned_count = sum(
            len(s["Action"]) for s in result["role_definition"]["policy"] if isinstance(s["Action"], list)
        )
        assert pruned_count == original_count

    def test_gcp_pruning_generates_warning_but_keeps_all(self):
        result = generate_role_from_template("gcp", "runtime_execution", ["storage"])
        assert result["status"] == "ok"
        assert len(result["warnings"]) == 1
        assert "GCP resource-type pruning not supported" in result["warnings"][0]
        assert len(result["role_definition"]["bindings"]) == 4

    def test_azure_monitor_with_insights_pruning(self):
        result = generate_role_from_template("azure", "monitor", ["insights"])
        actions = result["role_definition"]["Actions"]
        assert len(actions) > 0
        for a in actions:
            assert _azure_action_matches(a, {"insights"}), f"Action {a!r} does not match insights"

    def test_aws_pruning_preserves_non_action_statements(self):
        """Each statement is preserved; non-matching actions are pruned from lists."""
        result = generate_role_from_template("aws", "terraform_deploy", ["ec2"])
        policy = result["role_definition"]["policy"]
        assert len(policy) == 2
        assert "iam:PassRole" not in policy[1]["Action"]


class TestAzureActionMatching:
    def test_exact_prefix_match(self):
        assert _azure_action_matches("Microsoft.Compute/virtualMachines/read", {"compute"})

    def test_substring_in_action(self):
        assert _azure_action_matches("Microsoft.Storage/storageAccounts/blobServices/read", {"storage"})

    def test_no_match(self):
        assert not _azure_action_matches("Microsoft.Compute/virtualMachines/read", {"network"})

    def test_wildcard_resource(self):
        assert _azure_action_matches("Microsoft.Compute/*", {"compute"})

    def test_glob_action_matches_any(self):
        assert _azure_action_matches("Microsoft.Network/*", {"bogus"})

    def test_all_known_resource_types(self):
        known = [
            "compute",
            "network",
            "storage",
            "containerregistry",
            "containerservice",
            "app",
            "operationalinsights",
            "insights",
            "authorization",
            "managedidentity",
            "keyvault",
        ]
        for rt in known:
            assert _azure_action_matches(f"Microsoft.{rt.capitalize()}/something/read", {rt}), f"Failed for {rt}"

    def test_subscription_resource_type(self):
        assert _azure_action_matches("Microsoft.Resources/subscriptions/resourceGroups/*", {"resources"})

    def test_case_insensitive(self):
        assert _azure_action_matches("microsoft.compute/virtualmachines/read", {"compute"})

    def test_multiple_resource_types_partial(self):
        assert _azure_action_matches("Microsoft.Storage/blobServices/read", {"compute", "storage", "network"})


class TestAwsActionMatching:
    def test_prefix_match(self):
        assert _aws_action_matches("ec2:DescribeInstances", {"ec2"})

    def test_substring_in_action(self):
        assert _aws_action_matches("s3:GetObject", {"s3"})

    def test_no_match(self):
        assert not _aws_action_matches("ec2:DescribeInstances", {"s3"})

    def test_wildcard_resource_type(self):
        assert _aws_action_matches("ec2:DescribeInstances", {"*"})

    def test_action_without_colon(self):
        assert not _aws_action_matches("gibberish", {"ec2"})

    def test_case_insensitive(self):
        assert _aws_action_matches("S3:GetObject", {"s3"})

    def test_multiple_resource_types(self):
        assert _aws_action_matches("lambda:InvokeFunction", {"ec2", "lambda", "s3"})

    def test_exact_service_prefix_match(self):
        assert _aws_action_matches("cloudwatch:GetMetricData", {"cloudwatch"})

    def test_iam_actions(self):
        assert _aws_action_matches("iam:GetRole", {"iam"})
        assert _aws_action_matches("iam:PassRole", {"iam"})


class TestPruneByResourceTypes:
    def test_empty_resource_types_noop(self):
        role_def = {"Actions": ["a", "b"]}
        result, warnings = _prune_by_resource_types("azure", role_def, [])
        assert result == role_def
        assert warnings == []

    def test_azure_prunes_actions_list(self):
        role_def = {"Actions": ["Microsoft.Compute/read", "Microsoft.Network/read", "Microsoft.Storage/read"]}
        result, warnings = _prune_by_resource_types("azure", role_def, ["compute"])
        assert result["Actions"] == ["Microsoft.Compute/read"]
        assert len(warnings) == 1
        assert "Pruned 2" in warnings[0]

    def test_aws_prunes_policy_actions(self):
        role_def = {
            "policy": [
                {"Action": ["ec2:DescribeInstances", "s3:GetObject"], "Effect": "Allow", "Resource": "*"},
            ],
        }
        result, warnings = _prune_by_resource_types("aws", role_def, ["ec2"])
        assert result["policy"][0]["Action"] == ["ec2:DescribeInstances"]
        assert len(warnings) == 1
        assert "Pruned 1" in warnings[0]

    def test_aws_skips_non_dict_statements(self):
        role_def = {"policy": ["not a dict", {"Action": ["ec2:DescribeInstances"], "Effect": "Allow", "Resource": "*"}]}
        result, _warnings = _prune_by_resource_types("aws", role_def, ["ec2"])
        assert result["policy"][0] == "not a dict"
        assert result["policy"][1]["Action"] == ["ec2:DescribeInstances"]

    def test_gcp_returns_warning_unchanged(self):
        role_def = {"bindings": [{"role": "roles/compute.admin"}]}
        result, warnings = _prune_by_resource_types("gcp", role_def, ["storage"])
        assert result == role_def
        assert len(warnings) == 1
        assert "GCP resource-type pruning not supported" in warnings[0]

    def test_unknown_provider_noop(self):
        role_def = {"Actions": ["a", "b"]}
        result, warnings = _prune_by_resource_types("unknown", role_def, ["compute"])
        assert result == role_def
        assert warnings == []


class TestExports:
    def test_all_exports_correct_symbols(self):
        assert "ROLE_TEMPLATES" in __all__
        assert "generate_role_from_template" in __all__

    def test_all_is_tuple_or_list(self):
        assert isinstance(__all__, (list, tuple))

    def test_every_export_exists(self):
        import general_ludd.cloud.role_generator as mod

        for name in __all__:
            assert hasattr(mod, name), f"__all__ references {name!r} which doesn't exist"


class TestEdgeCases:
    def test_top_level_dict_isolation(self):
        r1 = generate_role_from_template("aws", "monitor")
        r2 = generate_role_from_template("aws", "monitor")
        assert r1 is not r2
        assert r1["role_definition"] is not r2["role_definition"]
        assert r1["role_definition"]["role_name"] == r2["role_definition"]["role_name"]

    def test_azure_model_inference_has_no_data_actions(self):
        result = generate_role_from_template("azure", "model_inference")
        assert result["role_definition"]["DataActions"] == []
        assert result["role_definition"]["NotDataActions"] == []

    def test_all_azure_roles_specify_assignable_scopes(self):
        for persona in ["terraform_deploy", "runtime_execution", "model_inference", "monitor"]:
            result = generate_role_from_template("azure", persona)
            assert len(result["role_definition"]["AssignableScopes"]) == 1
            assert "{subscription_id}" in result["role_definition"]["AssignableScopes"][0]

    def test_aws_monitor_has_deny_statement(self):
        result = generate_role_from_template("aws", "monitor")
        deny_stmts = [s for s in result["role_definition"]["policy"] if s["Effect"] == "Deny"]
        assert len(deny_stmts) == 1

    def test_aws_runtime_execution_has_deny_statement(self):
        result = generate_role_from_template("aws", "runtime_execution")
        deny_stmts = [s for s in result["role_definition"]["policy"] if s["Effect"] == "Deny"]
        assert len(deny_stmts) == 1

    def test_gcp_terraform_deploy_has_condition(self):
        result = generate_role_from_template("gcp", "terraform_deploy")
        bindings = result["role_definition"]["bindings"]
        conditions = [b for b in bindings if "condition" in b]
        assert len(conditions) == 1
        assert conditions[0]["condition"]["title"] == "only_sa"

    def test_gcp_runtime_has_deny_binding(self):
        result = generate_role_from_template("gcp", "runtime_execution")
        bindings = result["role_definition"]["bindings"]
        deny_bindings = [b for b in bindings if b.get("effect") == "deny"]
        assert len(deny_bindings) == 1
        assert "compute.instances.setMetadata" in deny_bindings[0]["permissions"]

    def test_azure_terraform_deploy_notactions_block_elevation(self):
        result = generate_role_from_template("azure", "terraform_deploy")
        not_actions = result["role_definition"]["NotActions"]
        assert "Microsoft.Authorization/roleAssignments/write" in not_actions
        assert "Microsoft.Authorization/roleDefinitions/write" in not_actions
