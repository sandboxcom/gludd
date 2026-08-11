"""Tests for cloud role generator — template-based IAM role generation."""

from __future__ import annotations

import pytest

from general_ludd.cloud.role_generator import (
    ROLE_TEMPLATES,
    _aws_action_matches,
    _azure_action_matches,
    _prune_by_resource_types,
    generate_role_from_template,
)


class TestRoleTemplates:
    def test_all_providers_present(self):
        assert "azure" in ROLE_TEMPLATES
        assert "aws" in ROLE_TEMPLATES
        assert "gcp" in ROLE_TEMPLATES

    def test_all_personas_present_for_azure(self):
        expected = {"terraform_deploy", "runtime_execution", "model_inference", "monitor"}
        assert set(ROLE_TEMPLATES["azure"].keys()) == expected

    def test_all_personas_present_for_aws(self):
        expected = {"terraform_deploy", "runtime_execution", "model_inference", "monitor"}
        assert set(ROLE_TEMPLATES["aws"].keys()) == expected

    def test_all_personas_present_for_gcp(self):
        expected = {"terraform_deploy", "runtime_execution", "model_inference", "monitor"}
        assert set(ROLE_TEMPLATES["gcp"].keys()) == expected

    @pytest.mark.parametrize(
        "provider,persona,expected_key",
        [
            ("azure", "terraform_deploy", "Name"),
            ("azure", "runtime_execution", "Actions"),
            ("azure", "model_inference", "Actions"),
            ("azure", "monitor", "Actions"),
            ("aws", "terraform_deploy", "policy"),
            ("aws", "runtime_execution", "policy"),
            ("gcp", "terraform_deploy", "bindings"),
            ("gcp", "runtime_execution", "bindings"),
        ],
    )
    def test_templates_have_expected_keys(self, provider, persona, expected_key):
        assert expected_key in ROLE_TEMPLATES[provider][persona]

    def test_azure_templates_have_runcommand_denied(self):
        personas_needing_it = {"terraform_deploy", "runtime_execution", "model_inference"}
        for persona, template in ROLE_TEMPLATES["azure"].items():
            not_actions = template.get("NotActions", [])
            has_runcommand = any("runCommand" in na for na in not_actions)
            if persona in personas_needing_it:
                assert has_runcommand, f"azure.{persona} lacks runCommand in NotActions"

    def test_aws_templates_have_iam_deny(self):
        personas_needing_it = {"runtime_execution", "model_inference", "monitor"}
        for persona, template in ROLE_TEMPLATES["aws"].items():
            policy = template.get("policy", [])
            deny_stmts = [s for s in policy if s.get("Effect") == "Deny"]
            if persona in personas_needing_it:
                assert len(deny_stmts) > 0, f"aws.{persona} lacks Deny statement"


class TestGenerateRoleFromTemplate:
    def test_unknown_provider_returns_error(self):
        result = generate_role_from_template("oracle", "terraform_deploy")
        assert result["status"] == "error"
        assert result["role_definition"] == {}
        assert len(result["warnings"]) == 1
        assert "oracle" in result["warnings"][0]

    def test_unknown_persona_returns_error(self):
        result = generate_role_from_template("azure", "super_admin")
        assert result["status"] == "error"
        assert result["role_definition"] == {}
        assert len(result["warnings"]) == 1
        assert "super_admin" in result["warnings"][0]
        assert "terraform_deploy" in result["warnings"][0]

    @pytest.mark.parametrize("provider", ["azure", "aws", "gcp"])
    def test_terraform_deploy_generates_ok(self, provider):
        result = generate_role_from_template(provider, "terraform_deploy")
        assert result["status"] == "ok"
        assert result["role_definition"]
        assert result["warnings"] == []

    @pytest.mark.parametrize("provider", ["azure", "aws", "gcp"])
    def test_runtime_execution_generates_ok(self, provider):
        result = generate_role_from_template(provider, "runtime_execution")
        assert result["status"] == "ok"
        assert result["role_definition"]

    @pytest.mark.parametrize("provider", ["azure", "aws", "gcp"])
    def test_model_inference_generates_ok(self, provider):
        result = generate_role_from_template(provider, "model_inference")
        assert result["status"] == "ok"
        assert result["role_definition"]

    @pytest.mark.parametrize("provider", ["azure", "aws", "gcp"])
    def test_monitor_generates_ok(self, provider):
        result = generate_role_from_template(provider, "monitor")
        assert result["status"] == "ok"
        assert result["role_definition"]

    def test_empty_string_provider_returns_error(self):
        result = generate_role_from_template("", "terraform_deploy")
        assert result["status"] == "error"

    def test_empty_string_persona_returns_error(self):
        result = generate_role_from_template("azure", "")
        assert result["status"] == "error"


class TestGenerateRoleFromTemplateWithResourceTypes:
    def test_none_resource_types_generates_ok(self):
        result = generate_role_from_template("azure", "monitor", None)
        assert result["status"] == "ok"
        assert result["warnings"] == []

    def test_empty_resource_types_generates_ok(self):
        result = generate_role_from_template("azure", "monitor", [])
        assert result["status"] == "ok"
        assert result["warnings"] == []

    def test_azure_prune_matching_resource_type_keeps_actions(self):
        result = generate_role_from_template("azure", "monitor", ["operationalinsights"])
        assert result["status"] == "ok"
        actions = result["role_definition"]["Actions"]
        assert any("OperationalInsights" in a for a in actions)

    def test_azure_prune_nonmatching_resource_type_removes_actions(self):
        result = generate_role_from_template("azure", "monitor", ["compute"])
        assert result["status"] == "ok"
        actions = result["role_definition"]["Actions"]
        assert not any("OperationalInsights" in a for a in actions)
        assert not any("Insights" in a for a in actions)

    def test_azure_prune_produces_warning_when_actions_removed(self):
        result = generate_role_from_template("azure", "terraform_deploy", ["compute"])
        if result["warnings"]:
            assert "Pruned" in result["warnings"][0]

    def test_aws_prune_matching_service_keeps_actions(self):
        result = generate_role_from_template("aws", "monitor", ["cloudwatch"])
        assert result["status"] == "ok"
        policy = result["role_definition"]["policy"]
        all_actions = [a for stmt in policy for a in stmt.get("Action", [])]
        assert any("cloudwatch:" in a for a in all_actions)

    def test_aws_prune_produces_warning_when_actions_removed(self):
        result = generate_role_from_template("aws", "terraform_deploy", ["ec2"])
        if result["warnings"]:
            assert "Pruned" in result["warnings"][0]

    def test_gcp_prune_warns_not_supported(self):
        result = generate_role_from_template("gcp", "terraform_deploy", ["compute"])
        assert any("GCP resource-type pruning not supported" in w for w in result["warnings"])

    def test_wildcard_asterisk_resource_type_keeps_all_azure_actions(self):
        full = generate_role_from_template("azure", "monitor", None)
        pruned = generate_role_from_template("azure", "monitor", ["*"])
        assert len(full["role_definition"]["Actions"]) == len(pruned["role_definition"]["Actions"])

    def test_wildcard_asterisk_resource_type_keeps_all_aws_actions(self):
        full = generate_role_from_template("aws", "monitor", None)
        pruned = generate_role_from_template("aws", "monitor", ["*"])
        full_policy = sum(len(stmt.get("Action", [])) for stmt in full["role_definition"]["policy"])
        pruned_policy = sum(len(stmt.get("Action", [])) for stmt in pruned["role_definition"]["policy"])
        assert full_policy == pruned_policy


class TestPruneByResourceTypes:
    def test_empty_resource_types_returns_unchanged(self):
        role_def = {"Actions": ["Microsoft.Compute/*"]}
        result_def, warnings = _prune_by_resource_types("azure", role_def, [])
        assert result_def == role_def
        assert warnings == []

    def test_azure_prune_microsoft_service_match(self):
        role_def = {
            "Actions": [
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Network/virtualNetworks/read",
                "Microsoft.Storage/storageAccounts/read",
            ]
        }
        result_def, _warnings = _prune_by_resource_types("azure", role_def, ["compute"])
        assert len(result_def["Actions"]) == 1
        assert "Microsoft.Compute/virtualMachines/read" in result_def["Actions"]

    def test_aws_prune_service_prefix_match(self):
        role_def = {"policy": [{"Effect": "Allow", "Action": ["ec2:*", "s3:*", "ecs:*"]}]}
        result_def, _warnings = _prune_by_resource_types("aws", role_def, ["ec2"])
        policy = result_def["policy"]
        assert len(policy[0]["Action"]) == 1
        assert "ec2:*" in policy[0]["Action"]

    def test_aws_prune_with_string_action(self):
        role_def = {"policy": [{"Effect": "Allow", "Action": "ec2:*"}]}
        result_def, _warnings = _prune_by_resource_types("aws", role_def, ["ec2"])
        assert result_def == role_def

    def test_aws_prune_skips_non_dict_statements(self):
        role_def = {
            "policy": [
                "not-a-dict",
                {"Effect": "Allow", "Action": ["ec2:*", "s3:*"]},
            ]
        }
        result_def, _warnings = _prune_by_resource_types("aws", role_def, ["ec2"])
        assert result_def["policy"][0] == "not-a-dict"
        assert len(result_def["policy"][1]["Action"]) == 1

    def test_gcp_prune_not_supported_warning(self):
        role_def = {"bindings": [{"role": "roles/compute.admin"}]}
        _result_def, warnings = _prune_by_resource_types("gcp", role_def, ["compute"])
        assert len(warnings) == 1
        assert "not supported" in warnings[0]

    def test_unknown_provider_passes_through(self):
        role_def = {"some_key": "value"}
        result_def, warnings = _prune_by_resource_types("unknown", role_def, ["compute"])
        assert result_def == role_def
        assert warnings == []

    def test_azure_no_actions_removed_when_all_match(self):
        role_def = {
            "Actions": [
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Compute/disks/read",
            ]
        }
        result_def, warnings = _prune_by_resource_types("azure", role_def, ["compute"])
        assert len(result_def["Actions"]) == 2
        assert warnings == []

    def test_azure_preserves_not_actions(self):
        role_def = {
            "Actions": ["Microsoft.Compute/*"],
            "NotActions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
        }
        result_def, _ = _prune_by_resource_types("azure", role_def, ["compute"])
        assert len(result_def["NotActions"]) == 1

    def test_aws_multiple_statements_each_pruned(self):
        role_def = {
            "policy": [
                {"Effect": "Allow", "Action": ["ec2:*", "s3:*"]},
                {"Effect": "Deny", "Action": ["iam:CreateUser", "iam:PassRole"]},
            ]
        }
        result_def, _warnings = _prune_by_resource_types("aws", role_def, ["ec2"])
        allow_actions = result_def["policy"][0]["Action"]
        assert len(allow_actions) == 1
        deny_actions = result_def["policy"][1]["Action"]
        assert len(deny_actions) == 0


class TestAzureActionMatches:
    def test_wildcard_resource_type_matches_anything(self):
        assert _azure_action_matches("Microsoft.Compute/virtualMachines/read", {"*"})

    def test_direct_resource_match(self):
        assert _azure_action_matches("Microsoft.Compute/virtualMachines/read", {"compute"})

    def test_direct_resource_mismatch(self):
        assert not _azure_action_matches("Microsoft.Compute/virtualMachines/read", {"storage"})

    def test_action_with_slash_star_matches(self):
        assert _azure_action_matches("Microsoft.Compute/*", {"anything"})

    def test_resource_name_in_action_lower(self):
        assert _azure_action_matches("Microsoft.ManagedIdentity/userAssignedIdentities/read", {"managedidentity"})

    def test_deep_path_match(self):
        assert _azure_action_matches(
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read", {"storage"}
        )

    def test_all_known_resource_types_match_their_prefix(self):
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
            action = f"microsoft.{rt}/some/resource"
            assert _azure_action_matches(action, {rt}), f"Failed for {rt}"


class TestAwsActionMatches:
    def test_wildcard_resource_type_matches_anything(self):
        assert _aws_action_matches("ec2:DescribeInstances", {"*"})

    def test_action_contains_resource_type(self):
        assert _aws_action_matches("s3:GetObject", {"s3"})

    def test_action_does_not_contain_resource_type(self):
        assert not _aws_action_matches("s3:GetObject", {"ec2"})

    def test_service_prefix_match(self):
        assert _aws_action_matches("ec2:DescribeInstances", {"ec2"})

    def test_service_prefix_mismatch(self):
        assert not _aws_action_matches("ec2:DescribeInstances", {"iam"})

    def test_multiple_resource_types_any_match(self):
        assert _aws_action_matches("ecs:RunTask", {"ec2", "ecs", "s3"})

    def test_no_colon_in_action_returns_false(self):
        assert not _aws_action_matches("bare_action", {"ec2"})


class TestGeneratedRolesAreValidStructures:
    def test_azure_role_has_all_required_keys(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = generate_role_from_template("azure", persona)
            rd = result["role_definition"]
            assert "Name" in rd, f"azure.{persona} missing Name"
            assert "Description" in rd, f"azure.{persona} missing Description"
            assert "Actions" in rd, f"azure.{persona} missing Actions"
            assert "NotActions" in rd, f"azure.{persona} missing NotActions"
            assert "AssignableScopes" in rd, f"azure.{persona} missing AssignableScopes"
            assert isinstance(rd["Actions"], list)
            assert isinstance(rd["NotActions"], list)

    def test_aws_role_has_all_required_keys(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = generate_role_from_template("aws", persona)
            rd = result["role_definition"]
            assert "role_name" in rd, f"aws.{persona} missing role_name"
            assert "description" in rd, f"aws.{persona} missing description"
            assert "policy" in rd, f"aws.{persona} missing policy"
            assert isinstance(rd["policy"], list)
            for stmt in rd["policy"]:
                assert isinstance(stmt, dict)
                assert "Effect" in stmt
                assert "Action" in stmt

    def test_gcp_role_has_all_required_keys(self):
        for persona in ("terraform_deploy", "runtime_execution", "model_inference", "monitor"):
            result = generate_role_from_template("gcp", persona)
            rd = result["role_definition"]
            assert "role_name" in rd, f"gcp.{persona} missing role_name"
            assert "description" in rd, f"gcp.{persona} missing description"
            assert "bindings" in rd, f"gcp.{persona} missing bindings"
            assert isinstance(rd["bindings"], list)


class TestDeepPruneScenarios:
    def test_azure_prune_all_actions_removed_returns_empty(self):
        role_def = {
            "Actions": [
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Network/virtualNetworks/read",
            ]
        }
        result_def, warnings = _prune_by_resource_types("azure", role_def, ["storage"])
        assert result_def["Actions"] == []
        assert len(warnings) == 1

    def test_aws_prune_all_actions_removed_returns_empty(self):
        role_def = {"policy": [{"Effect": "Allow", "Action": ["ec2:*"]}]}
        result_def, warnings = _prune_by_resource_types("aws", role_def, ["s3"])
        assert result_def["policy"][0]["Action"] == []
        assert len(warnings) == 1

    def test_multiple_resource_types_azure_keeps_intersection(self):
        role_def = {
            "Actions": [
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Network/virtualNetworks/read",
                "Microsoft.Storage/storageAccounts/read",
            ]
        }
        result_def, _ = _prune_by_resource_types("azure", role_def, ["compute", "storage"])
        assert len(result_def["Actions"]) == 2

    def test_multiple_resource_types_aws_keeps_intersection(self):
        role_def = {"policy": [{"Effect": "Allow", "Action": ["ec2:*", "ecs:*", "s3:*"]}]}
        result_def, _ = _prune_by_resource_types("aws", role_def, ["ec2", "s3"])
        assert len(result_def["policy"][0]["Action"]) == 2
