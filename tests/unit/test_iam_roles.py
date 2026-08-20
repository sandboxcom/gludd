"""Unit tests for IAM role definitions across AWS, GCP, and Azure.

Verifies each IAM file is valid YAML/JSON, enforces least-privilege rules
(no admin wildcards, actions scoped to specific services, descriptions
present, conditions for broad permissions), and pins the provider coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "config" / "infra"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def aws_doc() -> dict[str, Any]:
    path = INFRA_DIR / "aws-iam-roles.yml"
    assert path.exists(), f"Missing: {path}"
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


@pytest.fixture(scope="module")
def gcp_doc() -> dict[str, Any]:
    path = INFRA_DIR / "gcp-iam-roles.yml"
    assert path.exists(), f"Missing: {path}"
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


@pytest.fixture(scope="module")
def azure_doc() -> dict[str, Any]:
    path = INFRA_DIR / "azure-iam-roles.yml"
    assert path.exists(), f"Missing: {path}"
    return cast(dict[str, Any], yaml.safe_load(path.read_text()))


@pytest.fixture(scope="module")
def azure_custom_role() -> dict[str, Any]:
    path = INFRA_DIR / "azure-iam-policy.json"
    assert path.exists(), f"Missing: {path}"
    return cast(dict[str, Any], json.loads(path.read_text()))


# ---------------------------------------------------------------------------
# File existence and parseability
# ---------------------------------------------------------------------------


class TestFilesExistAndParseable:
    """Every IAM definition file must exist and parse as valid YAML/JSON."""

    def test_aws_iam_roles_exists(self, aws_doc: dict[str, Any]) -> None:
        assert isinstance(aws_doc, dict)

    def test_gcp_iam_roles_exists(self, gcp_doc: dict[str, Any]) -> None:
        assert isinstance(gcp_doc, dict)

    def test_azure_iam_roles_exists(self, azure_doc: dict[str, Any]) -> None:
        assert isinstance(azure_doc, dict)

    def test_azure_custom_role_json_exists(self, azure_custom_role: dict[str, Any]) -> None:
        assert isinstance(azure_custom_role, dict)
        assert "Name" in azure_custom_role

    def test_iam_readme_exists(self) -> None:
        path = INFRA_DIR / "IAM_README.md"
        assert path.exists(), f"Missing: {path}"
        content = path.read_text()
        assert "least-privilege" in content.lower()
        assert "aws" in content.lower()
        assert "gcp" in content.lower()
        assert "azure" in content.lower()


# ---------------------------------------------------------------------------
# No admin wildcards (forbidden: *:* or equivalent)
# ---------------------------------------------------------------------------


class TestNoAdminWildcards:
    """No policy may grant admin-equivalent wildcard access."""

    AWS_FORBIDDEN_PATTERNS = ("*:*",)
    AWS_FORBIDDEN_ACTION_PREFIXES = ("*",)  # bare "*" as an action is admin

    def test_aws_no_admin_wildcard(self, aws_doc: dict[str, Any]) -> None:
        roles = aws_doc["roles"]
        for role_name, role_def in roles.items():
            policy = role_def["policy"]
            for stmt in policy:
                if stmt.get("Effect") == "Deny":
                    continue
                actions = stmt.get("Action", [])
                for action in actions:
                    assert action != "*", (
                        f"AWS role '{role_name}' has admin wildcard '*' in Action — forbidden"
                    )
                    assert ":*" not in action or action.endswith(":*"), (
                        f"AWS role '{role_name}' has admin pattern in action: {action}"
                    )

    def test_gcp_no_admin_wildcard(self, gcp_doc: dict[str, Any]) -> None:
        GCP_FORBIDDEN = ("roles/owner", "roles/editor", "roles/viewer")
        roles = gcp_doc["roles"]
        for role_name, role_def in roles.items():
            for binding in role_def.get("roles", []):
                assert binding not in GCP_FORBIDDEN, (
                    f"GCP role '{role_name}' has forbidden binding '{binding}' — "
                    f"Owner/Editor/Viewer are admin-equivalent"
                )

    def test_azure_no_admin_wildcard(self, azure_doc: dict[str, Any]) -> None:
        AZURE_FORBIDDEN = ("Owner", "User Access Administrator")
        roles = azure_doc["roles"]
        for role_name, role_def in roles.items():
            for rd in role_def.get("role_definitions", []):
                assert rd not in AZURE_FORBIDDEN, (
                    f"Azure role '{role_name}' has forbidden built-in role '{rd}'"
                )

    def test_azure_custom_role_no_admin_wildcard(self, azure_custom_role: dict[str, Any]) -> None:
        actions = azure_custom_role.get("Actions", [])
        for action in actions:
            assert action != "*", "Azure custom role has admin wildcard '*' in Actions"
            assert action != "Microsoft.*/*", (
                "Azure custom role has admin wildcard 'Microsoft.*/*' in Actions"
            )


# ---------------------------------------------------------------------------
# Roles have descriptions
# ---------------------------------------------------------------------------


class TestRolesHaveDescriptions:
    """Every role must document its purpose."""

    MIN_DESC_LENGTH = 20

    def test_aws_roles_have_descriptions(self, aws_doc: dict[str, Any]) -> None:
        for role_name, role_def in aws_doc["roles"].items():
            desc = role_def.get("description", "")
            assert len(desc) >= self.MIN_DESC_LENGTH, (
                f"AWS role '{role_name}' description too short "
                f"({len(desc)} chars, need ≥{self.MIN_DESC_LENGTH})"
            )

    def test_gcp_roles_have_descriptions(self, gcp_doc: dict[str, Any]) -> None:
        for role_name, role_def in gcp_doc["roles"].items():
            desc = role_def.get("description", "")
            assert len(desc) >= self.MIN_DESC_LENGTH, (
                f"GCP role '{role_name}' description too short "
                f"({len(desc)} chars, need ≥{self.MIN_DESC_LENGTH})"
            )

    def test_azure_roles_have_descriptions(self, azure_doc: dict[str, Any]) -> None:
        for role_name, role_def in azure_doc["roles"].items():
            desc = role_def.get("description", "")
            assert len(desc) >= self.MIN_DESC_LENGTH, (
                f"Azure role '{role_name}' description too short "
                f"({len(desc)} chars, need ≥{self.MIN_DESC_LENGTH})"
            )


# ---------------------------------------------------------------------------
# Actions scoped to specific services (not bare '*')
# ---------------------------------------------------------------------------


class TestActionsScopedToServices:
    """AWS actions must name a service prefix (e.g. ec2:, s3:, not bare '*').

    GCP roles must be from allowed-service prefixes.
    Azure role definitions must be from the allowlisted set.
    """

    def test_aws_actions_have_service_prefix(self, aws_doc: dict[str, Any]) -> None:
        for role_name, role_def in aws_doc["roles"].items():
            policy = role_def["policy"]
            for stmt in policy:
                actions = stmt.get("Action", [])
                for action in actions:
                    assert ":" in action, (
                        f"AWS role '{role_name}': action '{action}' has no "
                        f"service prefix (expected e.g. 'ec2:Describe*')"
                    )

    def test_gcp_roles_from_allowed_services(self, gcp_doc: dict[str, Any]) -> None:
        ALLOWED_PREFIXES = (
            "roles/compute.",
            "roles/storage.",
            "roles/iam.",
            "roles/logging.",
            "roles/monitoring.",
            "roles/secretmanager.",
            "roles/artifactregistry.",
            "roles/aiplatform.",
            "roles/billing.",
            "roles/resourcemanager.",
        )
        for role_name, role_def in gcp_doc["roles"].items():
            for binding in role_def.get("roles", []):
                allowed = any(binding.startswith(prefix) for prefix in ALLOWED_PREFIXES)
                assert allowed, (
                    f"GCP role '{role_name}': binding '{binding}' not in "
                    f"allowlisted service prefixes"
                )

    def test_azure_role_definitions_from_allowlisted_set(self, azure_doc: dict[str, Any]) -> None:
        ALLOWED = frozenset({
            "General Ludd Accelerator Deployer",
            "Contributor",
            "Storage Blob Data Contributor",
            "Storage Blob Data Reader",
            "Key Vault Secrets User",
            "Virtual Machine Contributor",
            "Log Analytics Contributor",
            "AcrPull",
            "Cognitive Services User",
            "Cognitive Services Metrics Advisor User",
            "Monitoring Reader",
            "Cost Management Reader",
            "Service Health Reader",
        })
        for role_name, role_def in azure_doc["roles"].items():
            for rd in role_def.get("role_definitions", []):
                assert rd in ALLOWED, (
                    f"Azure role '{role_name}': '{rd}' not in allowlisted "
                    f"built-in/custom role set"
                )


# ---------------------------------------------------------------------------
# Conditions are present where broad permissions are granted
# ---------------------------------------------------------------------------


class TestConditionsForBroadPermissions:
    """When a policy grants broad scoped permissions, conditions MUST narrow them."""

    def test_aws_conditions_for_iam_passrole(self, aws_doc: dict[str, Any]) -> None:
        for role_name, role_def in aws_doc["roles"].items():
            policy = role_def["policy"]
            for stmt in policy:
                if stmt.get("Effect") == "Deny":
                    continue
                actions = stmt.get("Action", [])
                if "iam:PassRole" in actions:
                    assert "Condition" in stmt, (
                        f"AWS role '{role_name}' has iam:PassRole without "
                        f"a Condition block — must scope to specific role ARNs"
                    )

    def test_aws_conditions_for_runinstances(self, aws_doc: dict[str, Any]) -> None:
        tf_role = aws_doc["roles"]["terraform_deploy"]
        for stmt in tf_role["policy"]:
            actions = stmt.get("Action", [])
            if "ec2:RunInstances" in actions and stmt.get("Effect") == "Allow":
                assert "Condition" in stmt, (
                    "AWS terraform_deploy has ec2:RunInstances without a "
                    "Condition — must restrict instance types"
                )

    def test_gcp_conditions_for_terraform_deploy(self, gcp_doc: dict[str, Any]) -> None:
        tf_role = gcp_doc["roles"]["terraform_deploy"]
        assert len(tf_role.get("conditions", [])) > 0, (
            "GCP terraform_deploy has no conditions — must restrict instance "
            "types and zones"
        )

    def test_gcp_conditions_for_model_inference(self, gcp_doc: dict[str, Any]) -> None:
        mi_role = gcp_doc["roles"]["model_inference"]
        assert len(mi_role.get("conditions", [])) > 0, (
            "GCP model_inference has no conditions — must restrict to "
            "gludd-named endpoints"
        )

    def test_gcp_conditions_for_runtime_artifact_access(self, gcp_doc: dict[str, Any]) -> None:
        rt_role = gcp_doc["roles"]["runtime_execution"]
        assert len(rt_role.get("conditions", [])) > 0, (
            "GCP runtime_execution has no conditions — must restrict "
            "artifact and secret access"
        )

    def test_azure_conditions_for_broad_contributor(self, azure_doc: dict[str, Any]) -> None:
        tf_role = azure_doc["roles"]["terraform_deploy"]
        has_contrib = any("Contributor" in rd for rd in tf_role["role_definitions"])
        if has_contrib:
            assert "scope" in tf_role, (
                "Azure terraform_deploy has Contributor role but no scope — "
                "must scope to specific resource group"
            )
            assert "warning" in tf_role, (
                "Azure terraform_deploy has Contributor role but no warning "
                "about its breadth"
            )


# ---------------------------------------------------------------------------
# Provider coverage matrix
# ---------------------------------------------------------------------------


class TestProviderCoverage:
    """Every major PaaS provider from providers.yml must have an IAM roles file."""

    REQUIRED_PROVIDER_FILES: ClassVar[dict[str, str]] = {
        "aws": "aws-iam-roles.yml",
        "gcp": "gcp-iam-roles.yml",
        "azure": "azure-iam-roles.yml",
    }

    def test_major_providers_have_iam_files(self) -> None:
        for _provider, filename in self.REQUIRED_PROVIDER_FILES.items():
            path = INFRA_DIR / filename
            assert path.exists(), (
                f"Provider '{_provider}' missing IAM roles file: {filename}"
            )


# ---------------------------------------------------------------------------
# Required personas exist across all three providers
# ---------------------------------------------------------------------------


class TestRequiredPersonas:
    """Every provider must define the four standard personas."""

    REQUIRED_PERSONAS = frozenset({
        "terraform_deploy",
        "runtime_execution",
        "model_inference",
        "monitor",
    })

    def test_aws_defines_all_personas(self, aws_doc: dict[str, Any]) -> None:
        actual = frozenset(aws_doc["roles"].keys())
        missing = self.REQUIRED_PERSONAS - actual
        assert not missing, f"AWS missing personas: {missing}"

    def test_gcp_defines_all_personas(self, gcp_doc: dict[str, Any]) -> None:
        actual = frozenset(gcp_doc["roles"].keys())
        missing = self.REQUIRED_PERSONAS - actual
        assert not missing, f"GCP missing personas: {missing}"

    def test_azure_defines_all_personas(self, azure_doc: dict[str, Any]) -> None:
        actual = frozenset(azure_doc["roles"].keys())
        missing = self.REQUIRED_PERSONAS - actual
        assert not missing, f"Azure missing personas: {missing}"


# ---------------------------------------------------------------------------
# Azure custom role structural pin
# ---------------------------------------------------------------------------


class TestAzureCustomRoleStructure:
    """The azure-iam-policy.json custom role has required fields."""

    def test_custom_role_has_name(self, azure_custom_role: dict[str, Any]) -> None:
        assert isinstance(azure_custom_role.get("Name"), str)
        assert len(azure_custom_role["Name"]) > 0

    def test_custom_role_has_description(self, azure_custom_role: dict[str, Any]) -> None:
        assert isinstance(azure_custom_role.get("Description"), str)
        assert len(azure_custom_role["Description"]) > 0

    def test_custom_role_has_assignable_scopes(self, azure_custom_role: dict[str, Any]) -> None:
        scopes = azure_custom_role.get("AssignableScopes", [])
        assert isinstance(scopes, list) and len(scopes) > 0

    def test_custom_role_has_actions(self, azure_custom_role: dict[str, Any]) -> None:
        actions = azure_custom_role.get("Actions", [])
        assert isinstance(actions, list) and len(actions) > 0

    def test_custom_role_has_not_actions(self, azure_custom_role: dict[str, Any]) -> None:
        not_actions = azure_custom_role.get("NotActions", [])
        assert isinstance(not_actions, list)
        assert len(not_actions) > 0, (
            "Azure custom role has no NotActions — must explicitly deny "
            "dangerous operations"
        )

    def test_custom_role_has_no_denied_run_command(self, azure_custom_role: dict[str, Any]) -> None:
        not_actions = azure_custom_role.get("NotActions", [])
        run_cmd = "Microsoft.Compute/virtualMachines/runCommand/action"
        run_cmds_read = "Microsoft.Compute/virtualMachines/runCommands/read"
        assert run_cmd in not_actions, (
            f"Azure custom role must deny '{run_cmd}' in NotActions — "
            f"runCommand allows arbitrary code execution"
        )
        assert run_cmds_read in not_actions, (
            f"Azure custom role must deny '{run_cmds_read}' in NotActions"
        )

    def test_custom_role_data_actions_empty(self, azure_custom_role: dict[str, Any]) -> None:
        assert azure_custom_role.get("DataActions", []) == []
        assert azure_custom_role.get("NotDataActions", []) == []
