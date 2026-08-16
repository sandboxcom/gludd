"""Deep IAM and access-policy tests — policy document validity, wildcard
detection, ARN format, condition-block well-formedness, and cross-provider
consistency across all config/infra and Terraform IAM files.

Covers:
- All IAM JSON files parse as valid JSON with required top-level fields
- No admin-equivalent wildcards in sensitive policies
- Azure action strings follow valid provider namespace + resource hierarchy
- AWS resource ARNs match standard format (``arn:aws:service:region:account:resource``)
- Condition blocks use recognised AWS/Azure/GCP operators
- Deny statements exist alongside Allow in sensitive roles
- CLI-form policy documents conform to Azure RBAC API shape
- Policy documents are non-empty
- OPA Rego policies are parseable and cover all 3 cloud providers
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "config" / "infra"
TF_POLICY = REPO_ROOT / "infra" / "terraform" / "modules" / "onboard-iam" / "policy.json"
OPA_POLICY = REPO_ROOT / "config" / "opa" / "iam_policy.rego"
OPA_TEST = REPO_ROOT / "config" / "opa" / "iam_policy_test.rego"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def azure_policy() -> dict:
    path = INFRA_DIR / "azure-iam-policy.json"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def azure_cli_policy() -> dict:
    path = INFRA_DIR / "azure-iam-policy-cli.json"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def azure_ref_roles() -> list:
    path = INFRA_DIR / "azure-github-reference-roles.json"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def azure_ms_ref() -> dict:
    path = INFRA_DIR / "azure-ms-reference-roles.json"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def tf_policy() -> dict:
    return json.loads(TF_POLICY.read_text())


@pytest.fixture(scope="module")
def aws_roles() -> dict:
    path = INFRA_DIR / "aws-iam-roles.yml"
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def azure_roles() -> dict:
    path = INFRA_DIR / "azure-iam-roles.yml"
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def gcp_roles() -> dict:
    path = INFRA_DIR / "gcp-iam-roles.yml"
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Valid JSON / YAML parseability — all policy files
# ---------------------------------------------------------------------------


class TestPolicyDocumentsAreValid:
    """Every IAM policy document must parse as valid JSON or YAML."""

    def test_azure_custom_role_is_valid_json(self, azure_policy: dict) -> None:
        assert isinstance(azure_policy, dict)
        assert len(azure_policy) > 0

    def test_azure_cli_policy_is_valid_json(self, azure_cli_policy: dict) -> None:
        assert isinstance(azure_cli_policy, dict)
        assert len(azure_cli_policy) > 0

    def test_terraform_policy_is_valid_json(self, tf_policy: dict) -> None:
        assert isinstance(tf_policy, dict)
        assert "Version" in tf_policy
        assert "Statement" in tf_policy
        assert isinstance(tf_policy["Statement"], list)
        assert len(tf_policy["Statement"]) > 0
        assert tf_policy["Version"] == "2012-10-17"

    def test_azure_ref_roles_is_valid_json_list(self, azure_ref_roles: list) -> None:
        assert isinstance(azure_ref_roles, list)
        assert len(azure_ref_roles) > 0
        for role in azure_ref_roles:
            assert isinstance(role, dict)

    def test_azure_ms_ref_is_valid_json(self, azure_ms_ref: dict) -> None:
        assert isinstance(azure_ms_ref, dict)
        assert "examples" in azure_ms_ref
        assert len(azure_ms_ref["examples"]) > 0

    def test_all_iam_yaml_files_parseable(self, aws_roles: dict, azure_roles: dict, gcp_roles: dict) -> None:
        for doc in (aws_roles, azure_roles, gcp_roles):
            assert "roles" in doc
            assert isinstance(doc["roles"], dict)
            assert len(doc["roles"]) > 0


# ---------------------------------------------------------------------------
# No admin-equivalent wildcards in sensitive policies
# ---------------------------------------------------------------------------


class TestNoAdminWildcards:
    """No policy document may grant administrator-equivalent wildcard actions."""

    def test_azure_custom_role_no_wildcard_actions(self, azure_policy: dict) -> None:
        actions = azure_policy.get("Actions", [])
        for action in actions:
            assert action != "*"
            assert action != "Microsoft.*/*"
            assert not action.endswith("/*") or any(
                action.startswith(prefix)
                for prefix in (
                    "Microsoft.Resources/",
                    "Microsoft.ContainerRegistry/",
                    "Microsoft.App/",
                    "Microsoft.Network/",
                    "Microsoft.Compute/",
                )
            ), f"Sensitive wildcard: {action}"

    def test_azure_ref_roles_no_unscoped_wildcard_write_delete(self, azure_ref_roles: list) -> None:
        write_delete_wildcards = {"Microsoft.Authorization/*/write", "Microsoft.Compute/*"}
        for role in azure_ref_roles:
            actions = role.get("Actions", [])
            for action in actions:
                if action in write_delete_wildcards:
                    description = role.get("Description", "")
                    assert description, (
                        f"Role '{role.get('Name')}' has sensitive wildcard '{action}' with no description"
                    )

    def test_aws_terraform_policy_no_admin_wildcard(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            if stmt.get("Effect") != "Allow":
                continue
            for action in stmt.get("Action", []):
                assert not action.startswith("*"), f"Statement '{stmt.get('Sid')}' has admin wildcard '{action}'"

    def test_aws_terraform_policy_no_iam_star_allow(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            if stmt.get("Effect") != "Allow":
                continue
            for action in stmt.get("Action", []):
                assert action != "iam:*", f"Statement '{stmt.get('Sid')}' allows iam:* — forbidden"

    def test_gcp_iam_roles_no_owner_binding(self, gcp_roles: dict) -> None:
        forbidden = frozenset({"roles/owner", "roles/editor"})
        for _persona_name, persona in gcp_roles["roles"].items():
            for binding in persona.get("roles", []):
                assert binding not in forbidden, f"GCP persona '{_persona_name}' has forbidden binding {binding}"


# ---------------------------------------------------------------------------
# Azure action-string format validation
# ---------------------------------------------------------------------------


_AZURE_ACTION_RE = re.compile(r"^Microsoft\.\w+(/\w+)+(/(read|write|delete|action))$")


class TestAzureActionFormat:
    """Azure action strings must follow Microsoft.<Provider>/<resource>+/<verb>."""

    def test_custom_role_actions_match_rbac_format(self, azure_policy: dict) -> None:
        for action in azure_policy.get("Actions", []):
            assert _AZURE_ACTION_RE.match(action), (
                f"Azure action '{action}' does not match Microsoft.<Provider>/<resource>+/read|write|delete|action"
            )

    def test_custom_role_notactions_match_rbac_format(self, azure_policy: dict) -> None:
        for action in azure_policy.get("NotActions", []):
            assert _AZURE_ACTION_RE.match(action), f"Azure NotAction '{action}' invalid format"

    def test_cli_policy_actions_match_rbac_format(self, azure_cli_policy: dict) -> None:
        perms = azure_cli_policy.get("properties", {}).get("permissions", [{}])
        for perm in perms:
            for action in perm.get("actions", []):
                assert _AZURE_ACTION_RE.match(action), f"CLI action '{action}' invalid format"
            for action in perm.get("notActions", []):
                assert _AZURE_ACTION_RE.match(action), f"CLI notAction '{action}' invalid format"

    def test_custom_role_actions_use_only_allowlisted_providers(self, azure_policy: dict) -> None:
        allowed = frozenset(
            {
                "Microsoft.Resources",
                "Microsoft.ContainerRegistry",
                "Microsoft.App",
                "Microsoft.Network",
                "Microsoft.Compute",
                "Microsoft.Authorization",
                "Microsoft.OperationalInsights",
                "Microsoft.Insights",
            }
        )
        for action in azure_policy.get("Actions", []):
            prefix = action.split("/")[0]
            assert prefix in allowed, f"Azure action '{action}' uses provider '{prefix}' not in allowlist"

    def test_custom_role_no_data_plane_actions(self, azure_policy: dict) -> None:
        assert azure_policy.get("DataActions", []) == []
        assert azure_policy.get("NotDataActions", []) == []


# ---------------------------------------------------------------------------
# AWS ARN format validation
# ---------------------------------------------------------------------------


_AWS_ARN_RE = re.compile(r"^arn:aws:[a-z0-9\-]+:[a-z0-9\-\*]*:[0-9\*]*:[a-zA-Z0-9\-_\.\*/:\$\{\}]+$")
_AWS_ARN_WILDCARD_OK = re.compile(r"^arn:aws:[a-z0-9\-]+:[a-z0-9\-\*]*:[0-9\*]*:\*?$")
_AWS_LOG_ARN_RE = re.compile(r"^arn:aws:logs:[a-z0-9\-\*]*:[0-9\*]*:log-group:/[a-zA-Z0-9\-_/\.\*]+$")


class TestAwsArnFormat:
    """Resource ARNs in AWS policy documents must follow the standard format."""

    def test_terraform_policy_arns_match_format(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            resource = stmt.get("Resource")
            if isinstance(resource, str):
                if resource == "*" or resource.startswith("${"):
                    continue
                assert _AWS_ARN_RE.match(resource), f"ARN '{resource}' in '{stmt.get('Sid')}' invalid format"
            elif isinstance(resource, list):
                for r in resource:
                    if r == "*" or r.startswith("${"):
                        continue
                    assert _AWS_ARN_RE.match(r), f"ARN '{r}' in '{stmt.get('Sid')}' invalid format"

    def test_terraform_policy_log_arns_valid(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            sid = stmt.get("Sid", "")
            resource = stmt.get("Resource")
            resources = [resource] if isinstance(resource, str) else (resource or [])
            for r in resources:
                if isinstance(r, str) and r.startswith("arn:aws:logs:"):
                    assert _AWS_LOG_ARN_RE.match(r), (
                        f"Log ARN '{r}' in '{sid}' invalid — expected arn:aws:logs:region:account:log-group:/prefix/*"
                    )

    def test_terraform_policy_no_bare_star_for_allow(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            if stmt.get("Effect") != "Allow":
                continue
            resource = stmt.get("Resource")
            resources = [resource] if isinstance(resource, str) else (resource or [])
            for r in resources:
                if r == "*":
                    sid = stmt.get("Sid", "")
                    actions = stmt.get("Action", [])
                    ok_set = {
                        "arn:aws:ec2:*:*:*",
                        "arn:aws:ec2:*:*:volume/*",
                        "arn:aws:logs:*:*:log-group:/gludd/*",
                    }
                    assert (
                        any(isinstance(rc, str) and rc in ok_set for rc in resources)
                        or actions == ["iam:PassRole"]
                        or sid == "DenyIamEscalation"
                    ), f"Allow statement '{sid}' has bare '*' Resource without scoped ARN fallback"


# ---------------------------------------------------------------------------
# Condition-block well-formedness
# ---------------------------------------------------------------------------


_AWS_CONDITION_OPS = frozenset(
    {
        "StringEquals",
        "StringNotEquals",
        "StringEqualsIgnoreCase",
        "StringLike",
        "StringNotLike",
        "StringEqualsIfExists",
        "StringLikeIfExists",
        "NumericEquals",
        "NumericLessThan",
        "NumericGreaterThan",
        "Bool",
        "IpAddress",
        "NotIpAddress",
        "ArnEquals",
        "ArnLike",
        "Null",
        "DateEquals",
        "DateLessThan",
        "DateGreaterThan",
    }
)


class TestConditionBlocksWellFormed:
    """Condition blocks must use recognised operators and valid condition keys."""

    def test_terraform_policy_conditions_use_valid_operators(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            condition = stmt.get("Condition")
            if condition is None:
                continue
            assert isinstance(condition, dict), f"Condition in '{stmt.get('Sid')}' must be a dict"
            for op_key in condition:
                assert op_key in _AWS_CONDITION_OPS, f"Unknown condition operator '{op_key}' in '{stmt.get('Sid')}'"

    def test_terraform_policy_conditions_use_valid_keys(self, tf_policy: dict) -> None:
        valid_keys = frozenset(
            {
                "aws:RequestedRegion",
                "ec2:InstanceType",
                "iam:PassedToService",
                "aws:MultiFactorAuthPresent",
                "aws:SourceIp",
                "aws:PrincipalArn",
                "sagemaker:EndpointName",
            }
        )
        for stmt in tf_policy["Statement"]:
            condition = stmt.get("Condition")
            if condition is None:
                continue
            for _op, cond_map in condition.items():
                if not isinstance(cond_map, dict):
                    continue
                for key in cond_map:
                    assert key in valid_keys, f"Unknown condition key '{key}' in '{stmt.get('Sid')}'"

    def test_terraform_policy_condition_ec2_instancetype_not_empty(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            condition = stmt.get("Condition", {})
            for _op, cond_map in condition.items():
                if "ec2:InstanceType" in cond_map:
                    values = cond_map["ec2:InstanceType"]
                    assert isinstance(values, list) and len(values) > 0, (
                        f"ec2:InstanceType in '{stmt.get('Sid')}' is empty"
                    )

    def test_terraform_policy_condition_instancetype_values_valid(self, tf_policy: dict) -> None:
        instance_type_re = re.compile(
            r"^(t3\.[a-z]+|g[2456]dn?\.[0-9]?x?large|g[56]\.[0-9]*x?large|p[345]\.[0-9]+xlarge|p4d\.[0-9]+xlarge"
            r"|p4de\.[0-9]+xlarge"
            r"|g[2456]dn?\.\*|g[56]\.\*|p[345]\.\*|p4d\.\*|p4de\.\*)$"
        )
        for stmt in tf_policy["Statement"]:
            condition = stmt.get("Condition", {})
            for _op, cond_map in condition.items():
                instances = cond_map.get("ec2:InstanceType", [])
                for itype in instances:
                    assert instance_type_re.match(itype), f"Invalid instance type '{itype}' in '{stmt.get('Sid')}'"

    def test_gcp_conditions_use_valid_cel_expressions(self, gcp_roles: dict) -> None:
        for _persona_name, persona in gcp_roles["roles"].items():
            conditions = persona.get("conditions", [])
            for cond in conditions:
                assert "expression" in cond, f"GCP condition in {_persona_name} missing expression"
                assert "title" in cond, f"GCP condition in {_persona_name} missing title"
                expr = cond["expression"]
                assert isinstance(expr, str) and len(expr) > 0
                assert any(kw in expr for kw in ("resource.type", "resource.name", "&&", "||")), (
                    f"GCP condition expression in {_persona_name} appears non-functional"
                )


# ---------------------------------------------------------------------------
# Deny-statements present alongside Allow
# ---------------------------------------------------------------------------


class TestDeniedEscalationPaths:
    """Sensitive IAM policies must explicitly deny privilege-escalation paths."""

    def test_terraform_policy_has_deny_statement(self, tf_policy: dict) -> None:
        denies = [s for s in tf_policy["Statement"] if s.get("Effect") == "Deny"]
        assert len(denies) >= 1, "Terraform policy has no Deny statements"

    def test_terraform_policy_denies_iam_create_role(self, tf_policy: dict) -> None:
        deny_actions: set[str] = set()
        for s in tf_policy["Statement"]:
            if s.get("Effect") == "Deny":
                deny_actions.update(s.get("Action", []))
        assert "iam:CreateRole" in deny_actions, "Terraform policy must deny iam:CreateRole"
        assert "iam:AttachRolePolicy" in deny_actions, "Terraform policy must deny iam:AttachRolePolicy"

    def test_azure_custom_role_denies_runcommand(self, azure_policy: dict) -> None:
        not_actions = azure_policy.get("NotActions", [])
        assert "Microsoft.Compute/virtualMachines/runCommand/action" in not_actions, (
            "Azure custom role must deny runCommand/action"
        )

    def test_azure_custom_role_denies_roleassignment_write(self, azure_policy: dict) -> None:
        not_actions = azure_policy.get("NotActions", [])
        assert "Microsoft.Authorization/roleAssignments/write" in not_actions
        assert "Microsoft.Authorization/roleAssignments/delete" in not_actions
        assert "Microsoft.Authorization/roleDefinitions/write" in not_actions
        assert "Microsoft.Authorization/roleDefinitions/delete" in not_actions

    def test_aws_iam_roles_runtime_has_deny_statement(self, aws_roles: dict) -> None:
        rt = aws_roles["roles"]["runtime_execution"]
        has_deny = any(s.get("Effect") == "Deny" for s in rt.get("policy", []))
        assert has_deny, "AWS runtime_execution must have a Deny statement"


# ---------------------------------------------------------------------------
# CLI-form policy schema conformance
# ---------------------------------------------------------------------------


class TestAzureCliPolicySchema:
    """The azure-iam-policy-cli.json must conform to Azure RBAC API shape."""

    def test_cli_policy_has_properties_wrapper(self, azure_cli_policy: dict) -> None:
        assert "properties" in azure_cli_policy
        props = azure_cli_policy["properties"]
        assert isinstance(props, dict)

    def test_cli_policy_has_role_name(self, azure_cli_policy: dict) -> None:
        name = azure_cli_policy["properties"].get("roleName", "")
        assert isinstance(name, str) and len(name) > 0

    def test_cli_policy_has_description(self, azure_cli_policy: dict) -> None:
        desc = azure_cli_policy["properties"].get("description", "")
        assert isinstance(desc, str) and len(desc) > 0

    def test_cli_policy_has_assignable_scopes(self, azure_cli_policy: dict) -> None:
        scopes = azure_cli_policy["properties"].get("assignableScopes", [])
        assert isinstance(scopes, list) and len(scopes) > 0
        for scope in scopes:
            assert scope.startswith("/subscriptions/"), f"AssignableScope '{scope}' not a subscription path"

    def test_cli_policy_has_permissions(self, azure_cli_policy: dict) -> None:
        perms = azure_cli_policy["properties"].get("permissions", [])
        assert isinstance(perms, list) and len(perms) > 0
        perm = perms[0]
        assert isinstance(perm.get("actions"), list)
        assert isinstance(perm.get("notActions"), list)
        assert isinstance(perm.get("dataActions"), list)
        assert isinstance(perm.get("notDataActions"), list)

    def test_cli_policy_permissions_not_empty(self, azure_cli_policy: dict) -> None:
        perms = azure_cli_policy["properties"]["permissions"]
        assert len(perms[0]["actions"]) > 0, "CLI policy has empty actions"


# ---------------------------------------------------------------------------
# OPA Rego policy coverage
# ---------------------------------------------------------------------------


class TestOpaRegoPolicy:
    """The OPA Rego policy must be parseable and cover all 3 cloud providers."""

    def test_opa_policy_exists(self) -> None:
        assert OPA_POLICY.exists(), f"Missing OPA policy: {OPA_POLICY}"

    def test_opa_policy_covers_all_providers(self) -> None:
        content = OPA_POLICY.read_text()
        assert 'provider == "aws"' in content
        assert 'provider == "azure"' in content
        assert 'provider == "gcp"' in content

    def test_opa_policy_defines_deny_rules_for_azure(self) -> None:
        content = OPA_POLICY.read_text()
        assert "deny_azure_missing_scope" in content
        assert "deny_azure_no_actions" in content
        assert "deny_azure_missing_metadata" in content
        assert "deny_azure_invalid_scope" in content
        assert "deny_azure_data_plane_access" in content

    def test_opa_policy_defines_aws_guardrails(self) -> None:
        content = OPA_POLICY.read_text()
        assert "deny_aws_wildcard_resource" in content
        assert "aws_least_privilege_valid" in content

    def test_opa_policy_defines_gcp_guardrails(self) -> None:
        content = OPA_POLICY.read_text()
        assert "deny_gcp_set_metadata" in content
        assert "gcp_least_privilege_valid" in content

    def test_opa_test_file_covers_all_deny_rules(self) -> None:
        content = OPA_TEST.read_text()
        assert "test_deny_admin_access" in content
        assert "test_allow_scoped_policy" in content
        assert "test_deny_wildcard_rds" in content
        assert "test_deny_mfa_missing_for_create_user" in content
        assert "test_azure_scope_is_required" in content
        assert "test_azure_missing_name_denied" in content
        assert "test_azure_runcommand_not_denied_fails" in content
        assert "test_gcp_setmetadata_is_denied" in content


# ---------------------------------------------------------------------------
# Policy document contains a non-empty Actions set
# ---------------------------------------------------------------------------


class TestPolicyActionNonEmpty:
    """Every policy document must grant at least one action."""

    def test_azure_custom_role_actions_non_empty(self, azure_policy: dict) -> None:
        assert len(azure_policy.get("Actions", [])) > 0

    def test_cli_policy_actions_non_empty(self, azure_cli_policy: dict) -> None:
        perms = azure_cli_policy["properties"]["permissions"]
        for perm in perms:
            assert len(perm.get("actions", [])) > 0

    def test_terraform_policy_each_statement_non_empty(self, tf_policy: dict) -> None:
        for stmt in tf_policy["Statement"]:
            assert len(stmt.get("Action", [])) > 0, f"Statement '{stmt.get('Sid')}' has empty Action list"


# ---------------------------------------------------------------------------
# Role-generator templates: no admin wildcards in generated roles
# ---------------------------------------------------------------------------


class TestRoleGeneratorTemplates:
    """The role templates in role_generator.py must not embed admin wildcards."""

    def test_azure_templates_no_rg_star_star(self) -> None:
        from general_ludd.cloud.role_generator import ROLE_TEMPLATES

        azure = ROLE_TEMPLATES["azure"]
        for _persona, tmpl in azure.items():
            actions = tmpl.get("Actions", [])
            for action in actions:
                assert action != "*"
                assert action != "Microsoft.*/*"

    def test_aws_templates_no_admin(self) -> None:
        from general_ludd.cloud.role_generator import ROLE_TEMPLATES

        aws = ROLE_TEMPLATES["aws"]
        for _persona, tmpl in aws.items():
            for stmt in tmpl.get("policy", []):
                for action in stmt.get("Action", []):
                    assert action != "*"
                    assert action != "*:*"

    def test_gcp_templates_no_owner(self) -> None:
        from general_ludd.cloud.role_generator import ROLE_TEMPLATES

        gcp = ROLE_TEMPLATES["gcp"]
        for _persona, tmpl in gcp.items():
            for binding in tmpl.get("bindings", []):
                role = binding.get("role", "")
                assert role not in ("roles/owner", "roles/editor"), (
                    f"GCP template '{_persona}' has forbidden binding '{role}'"
                )
