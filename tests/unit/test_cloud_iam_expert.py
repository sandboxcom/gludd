"""Unit tests for ``general_ludd.cloud`` — unified cloud IAM expert system."""

from __future__ import annotations

import json

import pytest

from general_ludd.cloud.aws_validator import (
    AWS_REQUIRED_DENIALS,
    validate_aws_role,
)
from general_ludd.cloud.azure_validator import (
    azure_generate_portal_json,
    generate_role_definition,
    validate_action_string,
)
from general_ludd.cloud.contracts import (
    CloudFunction,
    CloudRoleDefinition,
    PersonaRoleMap,
    ValidationResult,
)
from general_ludd.cloud.core import (
    CROSS_PROVIDER_PATTERNS,
    generate_cloud_role,
    validate_cloud_role,
)
from general_ludd.cloud.gcp_validator import (
    GCP_REQUIRED_DENIALS,
    validate_gcp_role,
)
from general_ludd.cloud.role_generator import (
    ROLE_TEMPLATES,
)
from general_ludd.cloud.validate_all import validate_monitor_roles


class TestCloudContracts:
    def test_cloud_role_definition_defaults(self):
        crd = CloudRoleDefinition(provider="azure", name="test", description="A test role")
        assert crd.provider == "azure"
        assert crd.name == "test"
        assert crd.description == "A test role"
        assert crd.actions == []
        assert crd.not_actions == []
        assert crd.data_actions == []
        assert crd.not_data_actions == []
        assert crd.assignable_scopes == []

    def test_cloud_function_dataclass(self):
        cf = CloudFunction(
            provider="aws", name="iam:PassRole", category="iam", risk_level="high", required_denial="iam:PassRole"
        )
        assert cf.provider == "aws"
        assert cf.risk_level == "high"
        assert cf.required_denial == "iam:PassRole"

    def test_persona_role_map_roles_and_scopes(self):
        prm = PersonaRoleMap(
            persona="terraform_deploy",
            provider="azure",
            assignments=[
                ("Contributor", "/subscriptions/sub-1", True),
                ("User Access Administrator", "/subscriptions/sub-1", True),
            ],
        )
        assert prm.roles() == ["Contributor", "User Access Administrator"]
        assert prm.scopes() == ["/subscriptions/sub-1", "/subscriptions/sub-1"]

    def test_validation_result_fields(self):
        vr = ValidationResult(status="invalid", errors=["bad wildcard"], warnings=["tip: narrow scope"], provider="aws")
        assert vr.status == "invalid"
        assert len(vr.errors) == 1
        assert len(vr.warnings) == 1


class TestGenerateCloudRole:
    def test_generate_azure_terraform_deploy(self):
        result = generate_cloud_role("azure", "terraform_deploy")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        assert rd["Name"] == "custom-terraform-deploy"
        assert "Microsoft.Compute/*" in rd["Actions"]
        assert any("runCommand" in na for na in rd["NotActions"])

    def test_generate_azure_runtime_execution(self):
        result = generate_cloud_role("azure", "runtime_execution")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        assert rd["Name"] == "custom-runtime-execution"
        assert "Microsoft.ContainerRegistry/registries/pull/read" in rd["Actions"]

    def test_generate_azure_model_inference(self):
        result = generate_cloud_role("azure", "model_inference")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        assert "Microsoft.Storage/storageAccounts/blobServices/read" in rd["Actions"]

    def test_generate_azure_monitor(self):
        result = generate_cloud_role("azure", "monitor")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        assert "Microsoft.Insights/metrics/read" in rd["Actions"]

    def test_generate_aws_terraform_deploy(self):
        result = generate_cloud_role("aws", "terraform_deploy")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        assert rd["role_name"] == "terraform-deploy"
        assert any(
            "PassRole" in a
            for stmt in rd["policy"]
            for a in (stmt["Action"] if isinstance(stmt.get("Action"), list) else [])
        )

    def test_generate_aws_runtime_execution(self):
        result = generate_cloud_role("aws", "runtime_execution")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        has_deny = any(stmt.get("Effect") == "Deny" for stmt in rd["policy"])
        assert has_deny

    def test_generate_aws_model_inference(self):
        result = generate_cloud_role("aws", "model_inference")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        deny_actions = []
        for stmt in rd["policy"]:
            if stmt.get("Effect") == "Deny":
                deny_actions.extend(stmt.get("Action", []))
        assert "iam:PassRole" in deny_actions

    def test_generate_aws_monitor(self):
        result = generate_cloud_role("aws", "monitor")
        assert result["status"] in ("ok", "generated_with_warnings")
        rd = result["role_definition"]
        actions_all = [
            a for stmt in rd["policy"] for a in (stmt.get("Action", []) if isinstance(stmt.get("Action"), list) else [])
        ]
        assert any("cloudwatch:" in a for a in actions_all)

    def test_generate_gcp_terraform_deploy(self):
        result = generate_cloud_role("gcp", "terraform_deploy")
        assert result["status"] == "ok"
        rd = result["role_definition"]
        assert rd["role_name"] == "terraform-deploy"
        assert len(rd["bindings"]) >= 2

    def test_generate_gcp_runtime_execution(self):
        result = generate_cloud_role("gcp", "runtime_execution")
        assert result["status"] == "ok"
        bindings = result["role_definition"]["bindings"]
        has_deny = any(b.get("effect") == "deny" for b in bindings)
        assert has_deny

    def test_generate_gcp_model_inference(self):
        result = generate_cloud_role("gcp", "model_inference")
        assert result["status"] == "ok"
        bindings = result["role_definition"]["bindings"]
        has_deny = any(b.get("effect") == "deny" for b in bindings)
        assert has_deny

    def test_unknown_provider(self):
        result = generate_cloud_role("alibaba", "admin")
        assert result["status"] == "error"

    def test_unknown_persona(self):
        result = generate_cloud_role("aws", "super_admin")
        assert result["status"] == "error"


class TestValidateCloudRole:
    def test_validate_azure_valid_role(self):
        role = {
            "Name": "custom-test",
            "Description": "A test custom role for validation — at least 20 chars",
            "Actions": ["Microsoft.Compute/virtualMachines/read"],
            "NotActions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
            "AssignableScopes": ["/subscriptions/sub-id"],
            "DataActions": [],
            "NotDataActions": [],
        }
        result = validate_cloud_role("azure", role)
        assert result["status"] == "valid"

    def test_validate_azure_missing_field(self):
        role = {"Name": "bad-role", "Description": "Missing fields role for testing"}
        result = validate_cloud_role("azure", role)
        assert result["status"] == "invalid"

    def test_validate_azure_list_action_rejected(self):
        role = {
            "Name": "bad-actions",
            "Description": "A role with forbidden list/action suffix for testing",
            "Actions": ["Microsoft.Compute/virtualMachines/list/action"],
            "NotActions": [],
            "AssignableScopes": ["/subscriptions/sub-id"],
            "DataActions": [],
            "NotDataActions": [],
        }
        result = validate_cloud_role("azure", role)
        assert result["status"] == "invalid"

    def test_validate_aws_policy_list_required(self):
        role = {"role_name": "bad", "description": "A short desc"}
        result = validate_cloud_role("aws", role)
        assert result["status"] == "invalid"

    def test_validate_aws_admin_wildcard(self):
        role = {
            "role_name": "test",
            "description": "An IAM role with admin wildcard that should be rejected",
            "policy": [
                {"Effect": "Allow", "Action": ["*:*"], "Resource": "*"},
            ],
        }
        result = validate_cloud_role("aws", role)
        assert result["status"] == "invalid"

    def test_validate_aws_passrole_no_condition(self):
        role = {
            "role_name": "bad-passrole",
            "description": "A test IAM role with PassRole but no condition block",
            "policy": [
                {"Effect": "Allow", "Action": ["iam:PassRole"], "Resource": "*"},
            ],
        }
        result = validate_cloud_role("aws", role)
        assert result["status"] == "invalid"

    def test_validate_aws_runtime_no_deny(self):
        role = {
            "role_name": "runtime_execution",
            "description": "Runtime execution role missing a deny block for testing",
            "policy": [
                {"Effect": "Allow", "Action": ["ecr:GetAuthorizationToken"], "Resource": "*"},
            ],
        }
        result = validate_cloud_role("aws", role)
        assert result["status"] == "invalid"


class TestAzureValidatorReexports:
    def test_validate_action_string_ok(self):
        ok, _msg = validate_action_string("Microsoft.Compute/virtualMachines/read")
        assert ok is True

    def test_generate_role_definition(self):
        role = generate_role_definition(
            name="my-role",
            description="A generated role for testing purposes",
            providers=["Microsoft.Compute"],
            scope="/subscriptions/sub-id",
        )
        assert role["Name"] == "my-role"
        assert "Microsoft.Compute/virtualMachines/read" in role["Actions"]
        assert "Microsoft.Compute/virtualMachines/runCommand/action" in role["NotActions"]

    def test_azure_portal_json_wraps_properties(self):
        cli_role = {
            "Name": "portal-test",
            "Description": "A role for testing portal JSON generation",
            "Actions": ["Microsoft.Compute/virtualMachines/read"],
            "NotActions": [],
            "AssignableScopes": ["/subscriptions/sub-id"],
            "DataActions": [],
            "NotDataActions": [],
        }
        portal = azure_generate_portal_json(cli_role)
        assert "properties" in portal
        assert portal["properties"]["roleName"] == "portal-test"
        assert len(portal["properties"]["permissions"]) == 1
        assert "Microsoft.Compute/virtualMachines/read" in portal["properties"]["permissions"][0]["actions"]


class TestGcpValidator:
    def test_valid_gcp_role(self):
        bindings = [
            {"role": "roles/storage.objectViewer", "members": ["sa@proj.iam.gserviceaccount.com"]},
        ]
        result = validate_gcp_role("monitor", bindings)
        assert result["status"] == "valid"

    def test_owner_role_rejected(self):
        bindings = [
            {"role": "roles/owner", "members": ["user@domain.com"]},
        ]
        result = validate_gcp_role("terraform_deploy", bindings)
        assert result["status"] == "invalid"

    def test_setmetadata_allowed_no_condition(self):
        bindings = [
            {
                "role": "custom/allow-metadata",
                "permissions": ["compute.instances.setMetadata"],
                "members": ["sa@proj.iam.gserviceaccount.com"],
            },
        ]
        result = validate_gcp_role("model_inference", bindings)
        assert result["status"] == "invalid"

    def test_gcp_required_denials_present(self):
        assert "runtime_execution" in GCP_REQUIRED_DENIALS
        assert "compute.instances.setMetadata" in GCP_REQUIRED_DENIALS["runtime_execution"]


class TestAwsValidator:
    def test_aws_required_denials_present(self):
        assert "runtime_execution" in AWS_REQUIRED_DENIALS
        assert "iam:CreateUser" in AWS_REQUIRED_DENIALS["runtime_execution"]

    def test_valid_aws_role_passes(self):
        role = {
            "role_name": "terraform_deploy",
            "description": "A valid Terraform deploy role with at least twenty chars",
            "policy": [
                {"Effect": "Allow", "Action": ["ec2:DescribeInstances"], "Resource": "*"},
            ],
        }
        result = validate_aws_role(role)
        assert result["status"] == "valid"


class TestCrossProviderPatterns:
    def test_cross_provider_patterns_defined(self):
        assert "wildcard_resource_all" in CROSS_PROVIDER_PATTERNS
        assert "owner_role_assignment" in CROSS_PROVIDER_PATTERNS
        assert "passrole_unscoped" in CROSS_PROVIDER_PATTERNS

    def test_azure_generated_no_root_scope(self):
        result = generate_cloud_role("azure", "terraform_deploy")
        scopes = result["role_definition"].get("AssignableScopes", [])
        assert "/" not in scopes

    def test_azure_generated_role_rejects_get_action(self):
        result = generate_cloud_role("azure", "terraform_deploy")
        actions = result["role_definition"].get("Actions", [])
        for a in actions:
            assert not a.endswith("/get/action"), f"Action {a} uses invalid /get/action suffix"

    def test_aws_generated_passrole_has_condition(self):
        result = generate_cloud_role("aws", "terraform_deploy")
        for stmt in result["role_definition"].get("policy", []):
            if not isinstance(stmt, dict):
                continue
            actions = stmt.get("Action", [])
            if isinstance(actions, list) and "iam:PassRole" in actions and stmt.get("Effect") == "Allow":
                assert "Condition" in stmt, "PassRole Allow must have Condition"

    def test_gcp_generated_no_setmetadata(self):
        result = generate_cloud_role("gcp", "runtime_execution")
        for b in result["role_definition"].get("bindings", []):
            if isinstance(b, dict) and b.get("effect") != "deny":
                perms = b.get("permissions", [])
                assert "compute.instances.setMetadata" not in perms


class TestValidateAllCloudIam:
    def test_monitor_roles_generate_and_validate_for_every_provider(self, capsys: pytest.CaptureFixture[str]):
        assert validate_monitor_roles() == 0

        output = capsys.readouterr().out
        for provider in ("azure", "aws", "gcp"):
            assert f"{provider} monitor: generated=" in output
            assert "validated=valid" in output


class TestRoleTemplates:
    def test_all_providers_have_four_personas(self):
        expected = {"terraform_deploy", "runtime_execution", "model_inference", "monitor"}
        for provider in ("azure", "aws", "gcp"):
            assert set(ROLE_TEMPLATES[provider].keys()) == expected, f"{provider} missing personas"

    def test_templates_produce_valid_json(self):
        for provider in ROLE_TEMPLATES:
            for persona in ROLE_TEMPLATES[provider]:
                template = ROLE_TEMPLATES[provider][persona]
                json_str = json.dumps(template)
                assert json_str
                parsed = json.loads(json_str)
                assert parsed == template
