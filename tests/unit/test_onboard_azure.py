"""Unit tests for the Azure onboarding provider (`gludd onboard azure`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.onboard import azure as azure_onboard

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_MODULE_DIR = REPO_ROOT / "infra" / "terraform" / "modules" / "onboard-iam-azure"
AZURE_POLICY_PATH = REPO_ROOT / "config" / "infra" / "azure-iam-policy.json"
OPA_IAM_TEST_PATH = REPO_ROOT / "config" / "opa" / "iam_policy_test.rego"
ACCELERATOR_ROLE = "General Ludd Accelerator Deployer"
REQUIRED_ACCELERATOR_ACTIONS = (
    "Microsoft.Compute/skus/read",
    "Microsoft.Compute/locations/usages/read",
    "Microsoft.Compute/virtualMachines/extensions/read",
    "Microsoft.Compute/virtualMachines/extensions/write",
    "Microsoft.Compute/virtualMachines/extensions/delete",
)


# ---------------------------------------------------------------------------
# create_role_instructions
# ---------------------------------------------------------------------------

class TestCreateRoleInstructions:
    def test_mentions_terraform_apply(self) -> None:
        text = azure_onboard.create_role_instructions(subscription_id="00000000-0000-0000-0000-000000000000")
        assert "terraform init" in text.lower()
        assert "terraform apply" in text.lower()

    def test_mentions_principal_id(self) -> None:
        text = azure_onboard.create_role_instructions(subscription_id="00000000-0000-0000-0000-000000000000")
        assert "principal" in text.lower()

    def test_mentions_az_login(self) -> None:
        text = azure_onboard.create_role_instructions(subscription_id="00000000-0000-0000-0000-000000000000")
        assert "az login" in text

    def test_mentions_module_path(self) -> None:
        text = azure_onboard.create_role_instructions(subscription_id="00000000-0000-0000-0000-000000000000")
        assert "onboard-iam-azure" in text

    def test_mentions_service_principal_object_id_and_accelerator_role(self) -> None:
        text = azure_onboard.create_role_instructions(
            subscription_id="00000000-0000-0000-0000-000000000000",
        )
        assert "operator_principal_id" in text
        assert ACCELERATOR_ROLE in text


# ---------------------------------------------------------------------------
# token_acquisition_guide
# ---------------------------------------------------------------------------

class TestTokenAcquisitionGuide:
    def test_mentions_AZURE_CLIENT_ID(self) -> None:
        text = azure_onboard.token_acquisition_guide()
        assert "AZURE_CLIENT_ID" in text

    def test_mentions_all_required_env_vars(self) -> None:
        text = azure_onboard.token_acquisition_guide()
        for var in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"):
            assert var in text, f"Missing env var mention: {var}"

    def test_mentions_app_registration_or_managed_identity(self) -> None:
        text = azure_onboard.token_acquisition_guide()
        lowered = text.lower()
        assert "app registration" in lowered or "managed identity" in lowered

    def test_service_principal_guide_assigns_accelerator_role(self) -> None:
        text = azure_onboard.token_acquisition_guide()
        assert "az ad sp show" in text
        assert "operator_principal_id" in text
        assert ACCELERATOR_ROLE in text


# ---------------------------------------------------------------------------
# validate_token_and_role
# ---------------------------------------------------------------------------

class TestValidateTokenAndRole:
    def test_calls_virtual_machines_list(self) -> None:
        """Validate probes compute.virtual_machines.list."""
        fake_compute = MagicMock()
        fake_compute.virtual_machines.list.return_value = MagicMock(
            next=MagicMock(),  # iterator
        )

        with patch.object(azure_onboard, "_build_azure_client", return_value=fake_compute), \
             patch.object(azure_onboard, "_get_role_assignments", return_value=[]):
            _ok, info = azure_onboard.validate_token_and_role(
                subscription_id="00000000-0000-0000-0000-000000000000",
                resource_group_name="gludd-rg",
                principal_id="11111111-1111-1111-1111-111111111111",
            )

        fake_compute.virtual_machines.list.assert_called_once()
        assert info["subscription"] == "00000000-0000-0000-0000-000000000000"

    def test_returns_missing_roles_when_empty(self) -> None:
        fake_compute = MagicMock()
        fake_compute.virtual_machines.list.return_value = MagicMock(next=MagicMock())

        with patch.object(azure_onboard, "_build_azure_client", return_value=fake_compute), \
             patch.object(azure_onboard, "_get_role_assignments", return_value=[]):
            ok, info = azure_onboard.validate_token_and_role(
                subscription_id="sub-1",
                resource_group_name="rg",
                principal_id="principal-1",
            )

        assert ok is False
        assert len(info["missing"]) > 0
        for role in info["missing"]:
            assert role  # non-empty names

    def test_ok_when_all_roles_present(self) -> None:
        fake_compute = MagicMock()
        fake_compute.virtual_machines.list.return_value = MagicMock(next=MagicMock())

        all_assignments = [
            {"role_definition_name": r, "principal_id": "principal-1"}
            for r in azure_onboard.EXPECTED_ROLES
        ]
        with patch.object(azure_onboard, "_build_azure_client", return_value=fake_compute), \
             patch.object(azure_onboard, "_get_role_assignments", return_value=all_assignments):
            ok, info = azure_onboard.validate_token_and_role(
                subscription_id="sub-1",
                resource_group_name="rg",
                principal_id="principal-1",
            )

        assert ok is True
        assert info["missing"] == []
        assert set(info["roles_verified"]) == set(azure_onboard.EXPECTED_ROLES)

    def test_requires_principal_id(self) -> None:
        with pytest.raises(ValueError, match="principal_id is required"):
            azure_onboard.validate_token_and_role(subscription_id="sub-1")


class TestAzureSdkBoundaries:
    @staticmethod
    def _sdk_modules() -> tuple[dict[str, ModuleType], MagicMock, MagicMock]:
        azure = ModuleType("azure")
        identity = ModuleType("azure.identity")
        mgmt = ModuleType("azure.mgmt")
        compute = ModuleType("azure.mgmt.compute")
        authorization = ModuleType("azure.mgmt.authorization")
        credential = MagicMock(name="DefaultAzureCredential")
        compute_client = MagicMock(name="ComputeManagementClient")
        identity.DefaultAzureCredential = credential
        compute.ComputeManagementClient = compute_client
        return (
            {
                "azure": azure,
                "azure.identity": identity,
                "azure.mgmt": mgmt,
                "azure.mgmt.compute": compute,
                "azure.mgmt.authorization": authorization,
            },
            credential,
            compute_client,
        )

    def test_build_client_uses_default_credential_and_subscription(self) -> None:
        modules, credential, compute_client = self._sdk_modules()
        with patch.dict(sys.modules, modules):
            result = azure_onboard._build_azure_client(subscription_id="sub-1")

        assert result is compute_client.return_value
        compute_client.assert_called_once_with(
            credential=credential.return_value,
            subscription_id="sub-1",
        )

    def test_role_assignments_are_normalized(self) -> None:
        modules, credential, _compute_client = self._sdk_modules()
        auth_client_type = MagicMock(name="AuthorizationManagementClient")
        modules["azure.mgmt.authorization"].AuthorizationManagementClient = (
            auth_client_type
        )
        auth_client = auth_client_type.return_value
        auth_client.role_assignments.list.return_value = [
            SimpleNamespace(
                principal_id="principal-1",
                role_definition_id="/subscriptions/sub-1/roleDefinitions/role-1",
            ),
        ]

        with (
            patch.dict(sys.modules, modules),
            patch.object(
                azure_onboard,
                "_resolve_role_definition_name",
                return_value="General Ludd Accelerator Deployer",
            ),
        ):
            result = azure_onboard._get_role_assignments("sub-1", "principal-1")

        assert result == [{
            "principal_id": "principal-1",
            "role_definition_id": "/subscriptions/sub-1/roleDefinitions/role-1",
            "role_definition_name": "General Ludd Accelerator Deployer",
        }]
        auth_client_type.assert_called_once_with(
            credential=credential.return_value,
            subscription_id="sub-1",
        )
        auth_client.role_assignments.list.assert_called_once_with(
            filter="principalId eq 'principal-1'",
        )

    def test_role_definition_name_handles_success_empty_and_failure(self) -> None:
        auth_client = MagicMock()
        auth_client.role_definitions.get.return_value.role_name = "Accelerator"
        role_id = "/subscriptions/sub-1/roleDefinitions/role-1"

        assert (
            azure_onboard._resolve_role_definition_name(auth_client, role_id)
            == "Accelerator"
        )
        assert azure_onboard._resolve_role_definition_name(auth_client, "") == ""
        auth_client.role_definitions.get.side_effect = RuntimeError("unavailable")
        assert azure_onboard._resolve_role_definition_name(auth_client, role_id) == ""


# ---------------------------------------------------------------------------
# Terraform module — least-privilege IAM policy
# ---------------------------------------------------------------------------

class TestTerraformModuleLeastPriv:
    def test_module_files_exist(self) -> None:
        assert AZURE_MODULE_DIR.is_dir(), f"Missing module dir: {AZURE_MODULE_DIR}"
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            assert (AZURE_MODULE_DIR / name).is_file(), f"Missing {name}"

    def test_iam_policy_uses_custom_accelerator_role(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        assert 'resource "azurerm_role_definition" "accelerator_deployer"' in main_tf
        assert ACCELERATOR_ROLE in main_tf

        # Forbidden broad roles.
        for bad in ('role_definition_name = "Contributor"', 'role_definition_name = "Owner"'):
            assert bad not in main_tf, f"Forbidden broad built-in role {bad} present in main.tf"

    def test_policy_and_module_cover_preflight_and_gpu_driver_extensions(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        policy = json.loads(AZURE_POLICY_PATH.read_text())

        assert policy["Name"] == ACCELERATOR_ROLE
        for action in REQUIRED_ACCELERATOR_ACTIONS:
            assert action in main_tf
            assert action in policy["Actions"]

    def test_role_can_target_service_principal_or_managed_identity(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        variables_tf = (AZURE_MODULE_DIR / "variables.tf").read_text()

        assert 'variable "operator_principal_id"' in variables_tf
        assert "var.operator_principal_id" in main_tf
        assert "azurerm_user_assigned_identity.gludd_operator.principal_id" in main_tf

    def test_opa_contract_checks_accelerator_role_subscription_scope(self) -> None:
        rego_tests = OPA_IAM_TEST_PATH.read_text()
        makefile = (REPO_ROOT / "Makefile").read_text()

        assert "test_azure_accelerator_role_subscription_scope_passes" in rego_tests
        assert ACCELERATOR_ROLE in rego_tests
        assert '"/subscriptions/sub-123"' in rego_tests
        assert "test-opa-policies:" in makefile

    def test_creates_user_assigned_identity(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        assert "azurerm_user_assigned_identity" in main_tf
        assert "gludd-compute-operator" in main_tf

    def test_outputs_principal_and_client_id(self) -> None:
        outputs_tf = (AZURE_MODULE_DIR / "outputs.tf").read_text()
        assert "principal_id" in outputs_tf
        assert "client_id" in outputs_tf
        assert "tenant_id" in outputs_tf


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
