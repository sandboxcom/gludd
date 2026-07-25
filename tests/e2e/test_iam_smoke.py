"""E2E smoke tests for IAM Terraform modules — vendor-specific least-privilege.

Validates AWS, Azure, and GCP onboarding IAM modules for:
  - No wildcard actions/resources in policy documents
  - No forbidden broad roles (Contributor, Owner, Editor)
  - Specific denies for privilege-escalation vectors (setMetadata, PassRole)
  - Scope/region conditions present
  - terraform validate passes for each module
  - Cross-cloud aggregate least-privilege
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES = REPO_ROOT / "infra" / "terraform" / "modules"
AWS_MODULE = MODULES / "onboard-iam"
AZURE_MODULE = MODULES / "onboard-iam-azure"
GCP_MODULE = MODULES / "onboard-iam-gcp"
OPA_POLICY = REPO_ROOT / "config" / "opa" / "iam_policy.rego"


def _infra_binary() -> str | None:
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    return None


# ============================================================================
# AWS — policy document structure + least-privilege
# ============================================================================


class TestAwsPolicyDocument:
    """Structural checks on the AWS IAM policy JSON."""

    @pytest.fixture(scope="class")
    def policy(self) -> dict:
        return json.loads((AWS_MODULE / "policy.json").read_text())

    def test_policy_has_no_wildcard_actions(self, policy: dict) -> None:
        for stmt in policy["Statement"]:
            acts = stmt.get("Action", [])
            if isinstance(acts, str):
                acts = [acts]
            for a in acts:
                assert "*" not in a, (
                    f"Wildcard action '{a}' in statement {stmt.get('Sid')}"
                )

    def test_policy_has_no_wildcard_resources(self, policy: dict) -> None:
        """Allowed resources must not be bare '*'."""
        for stmt in policy["Statement"]:
            if stmt.get("Effect") != "Allow":
                continue
            res = stmt.get("Resource", [])
            if isinstance(res, str):
                res = [res]
            for r in res:
                assert r != "*", (
                    f"Wildcard resource '*' in statement {stmt.get('Sid')}"
                )

    def test_passrole_is_self_only(self, policy: dict) -> None:
        """iam:PassRole must be scoped to the operator role ARN, not *."""
        for stmt in policy["Statement"]:
            acts = stmt.get("Action", [])
            if isinstance(acts, str):
                acts = [acts]
            if "iam:PassRole" in acts:
                res = stmt.get("Resource", [])
                if isinstance(res, str):
                    res = [res]
                for r in res:
                    assert r != "*", (
                        f"PassRole resource is '{r}' — must be role-ARN-scoped"
                    )
                    assert "operator_role_arn" in r.lower() or "role" in r.lower(), (
                        f"PassRole resource '{r}' does not scope to operator role"
                    )

    def test_deny_block_exists(self, policy: dict) -> None:
        """An explicit Deny block must forbid IAM escalation actions."""
        deny_stmts = [s for s in policy["Statement"] if s.get("Effect") == "Deny"]
        assert len(deny_stmts) >= 1, "Missing explicit Deny statement"

        all_deny_actions: set[str] = set()
        for s in deny_stmts:
            acts = s.get("Action", [])
            if isinstance(acts, str):
                acts = [acts]
            all_deny_actions.update(acts)
        assert "iam:CreateUser" in all_deny_actions
        assert "iam:CreateRole" in all_deny_actions

    def test_no_iam_or_sts_wildcards(self, policy: dict) -> None:
        """No iam:* or sts:* anywhere in the policy."""
        for stmt in policy["Statement"]:
            acts = stmt.get("Action", [])
            if isinstance(acts, str):
                acts = [acts]
            for a in acts:
                assert a not in ("iam:*", "sts:*"), (
                    f"Wildcard '{a}' is forbidden — use scoped actions"
                )

    def test_ec2_instance_type_condition_present(self, policy: dict) -> None:
        """PassRole or core statements should have InstanceType conditions."""
        found_condition = False
        for stmt in policy["Statement"]:
            if stmt.get("Condition"):
                found_condition = True
                cond = stmt["Condition"]
                # Check for ec2:InstanceType condition.
                for key in cond:
                    assert "InstanceType" in str(cond[key]), (
                        f"Expected ec2:InstanceType condition in {stmt.get('Sid')}"
                    )
        assert found_condition, "No Condition blocks found in policy — add InstanceType allowlist"


# ============================================================================
# Azure — role assignment scope + forbidden roles
# ============================================================================


class TestAzureRoleAssignments:
    """Least-privilege checks on the Azure IAM module."""

    @pytest.fixture(scope="class")
    def main_tf(self) -> str:
        return (AZURE_MODULE / "main.tf").read_text()

    def test_no_contributor_or_owner(self, main_tf: str) -> None:
        for forbidden in ('"Contributor"', '"Owner"', '"User Access Administrator"'):
            assert forbidden not in main_tf, f"Forbidden role {forbidden} in Azure module"

    def test_scope_is_set_to_subscription(self, main_tf: str) -> None:
        """Scope must be explicit — controls billing account."""
        assert 'scope' in main_tf, "Missing 'scope' on role assignments"
        # The scope must reference var.subscription_id.
        assert "subscription_id" in main_tf, (
            "Must reference var.subscription_id for scope"
        )
        scope_matches = re.findall(
            r'scope\s*=\s*"/subscriptions/\$\{(var\.\w+)\}"', main_tf,
        )
        assert len(scope_matches) >= 1, (
            f"Expected scope=/subscriptions/${{var.subscription_id}}, found: {scope_matches}"
        )

    def test_uses_managed_identity_not_service_principal(self, main_tf: str) -> None:
        """Should use user-assigned managed identity, not service principal."""
        assert "azurerm_user_assigned_identity" in main_tf
        # Service principal with password is broader and harder to rotate.
        assert "password" not in main_tf.lower() or "client_secret" not in main_tf.lower()

    def test_subscription_id_variable_exists(self) -> None:
        vars_tf = (AZURE_MODULE / "variables.tf").read_text()
        assert 'variable "subscription_id"' in vars_tf


# ============================================================================
# GCP — custom role denies setMetadata, no editor/owner
# ============================================================================


class TestGcpCustomRole:
    """Least-privilege checks on the GCP IAM module."""

    @pytest.fixture(scope="class")
    def main_tf(self) -> str:
        return (GCP_MODULE / "main.tf").read_text()

    def test_no_owner_or_editor(self, main_tf: str) -> None:
        for bad in ("roles/owner", "roles/editor", "roles/viewer",
                     "roles/compute.admin", "roles/compute.instanceAdmin.v1"):
            assert bad not in main_tf, (
                f"Forbidden/broad role '{bad}' in GCP module"
            )

    def test_no_setmetadata_permission(self, main_tf: str) -> None:
        """compute.instances.setMetadata must NOT be granted (SSH key injection risk)."""
        assert "compute.instances.setMetadata" not in main_tf, (
            "compute.instances.setMetadata grants SSH key injection — must not be allowed"
        )

    def test_custom_role_declared(self, main_tf: str) -> None:
        """Custom role replaces compute.instanceAdmin.v1."""
        assert "gluddComputeOperator" in main_tf, (
            "Missing gluddComputeOperator custom role"
        )
        assert "google_project_iam_custom_role" in main_tf

    def test_custom_role_has_compute_permissions(self, main_tf: str) -> None:
        """Custom role must include essential instance management permissions."""
        required = [
            "compute.instances.insert",
            "compute.instances.delete",
            "compute.instances.start",
            "compute.instances.stop",
            "compute.disks.create",
            "compute.disks.delete",
        ]
        for perm in required:
            assert perm in main_tf, f"Missing required permission: {perm}"


# ============================================================================
# Cross-cloud — all three modules pass least-privilege
# ============================================================================


class TestCrossCloudLeastPrivilege:
    """All three IAM modules must pass least-privilege checks."""

    def test_all_modules_exist(self) -> None:
        for mod in (AWS_MODULE, AZURE_MODULE, GCP_MODULE):
            assert mod.is_dir(), f"Module dir missing: {mod}"
            for name in ("main.tf", "variables.tf", "outputs.tf"):
                assert (mod / name).exists(), f"{mod.name}/{name} missing"

    def test_opa_policy_covers_all_clouds(self) -> None:
        """OPA rego policy must have rules for all three clouds."""
        rego = OPA_POLICY.read_text()
        for cloud in ("aws_least_privilege_valid", "azure_least_privilege_valid",
                       "gcp_least_privilege_valid", "all_clouds_least_privilege_valid"):
            assert cloud in rego, f"OPA policy missing rule: {cloud}"

    def test_opa_policy_has_azure_scope_check(self) -> None:
        rego = OPA_POLICY.read_text()
        assert "deny_azure_missing_scope" in rego, "OPA missing Azure scope rule"

    def test_opa_policy_has_gcp_setmetadata_check(self) -> None:
        rego = OPA_POLICY.read_text()
        assert "deny_gcp_set_metadata" in rego, "OPA missing GCP setMetadata rule"


# ============================================================================
# Terraform validate — each module
# ============================================================================


@pytest.mark.skipif(_infra_binary() is None, reason="terraform/tofu not on PATH")
class TestTerraformValidate:
    """terraform validate passes for each IAM module."""

    @staticmethod
    def _validate_module(module_dir: Path, binary: str) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for f in module_dir.iterdir():
                if f.is_file():
                    shutil.copy(f, tmp_path / f.name)
            init = subprocess.run(
                [binary, "init", "-backend=false"],
                cwd=tmp_path, capture_output=True, text=True, timeout=120,
            )
            assert init.returncode == 0, (
                f"terraform init failed for {module_dir.name}:\n{init.stderr}"
            )
            validate = subprocess.run(
                [binary, "validate"],
                cwd=tmp_path, capture_output=True, text=True, timeout=60,
            )
            assert validate.returncode == 0, (
                f"terraform validate failed for {module_dir.name}:\n{validate.stderr}"
            )

    def test_aws_module_validates(self) -> None:
        binary = _infra_binary()
        assert binary is not None
        self._validate_module(AWS_MODULE, binary)

    def test_azure_module_validates(self) -> None:
        binary = _infra_binary()
        assert binary is not None
        self._validate_module(AZURE_MODULE, binary)

    def test_gcp_module_validates(self) -> None:
        binary = _infra_binary()
        assert binary is not None
        self._validate_module(GCP_MODULE, binary)
