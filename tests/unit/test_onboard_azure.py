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
SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
RESOURCE_GROUP_SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
)
ROLE_DEFINITION_ID = "96008390-cad3-42f5-b72a-b5230b176675"
REQUIRED_ACCELERATOR_ACTIONS = (
    "Microsoft.App/managedEnvironments/read",
    "Microsoft.App/managedEnvironments/write",
    "Microsoft.App/managedEnvironments/delete",
    "Microsoft.App/managedEnvironments/join/action",
    "Microsoft.App/managedEnvironments/usages/read",
    "Microsoft.App/managedEnvironments/workloadProfileStates/read",
    "Microsoft.App/containerApps/read",
    "Microsoft.App/containerApps/write",
    "Microsoft.App/containerApps/delete",
    "Microsoft.App/containerApps/revisions/read",
    "Microsoft.App/locations/containerAppOperationResults/read",
    "Microsoft.App/locations/containerAppOperationStatuses/read",
    "Microsoft.App/locations/managedEnvironmentOperationResults/read",
    "Microsoft.App/locations/managedEnvironmentOperationStatuses/read",
    "Microsoft.Insights/metrics/read",
)
OBSOLETE_PROVIDER_REGISTRATION = "Microsoft.Resources/subscriptions/providers/register/action"


# ---------------------------------------------------------------------------
# create_role_instructions
# ---------------------------------------------------------------------------

class TestCreateRoleInstructions:
    def test_makes_sdk_role_apply_the_canonical_local_bootstrap(self) -> None:
        text = azure_onboard.create_role_instructions(subscription_id="00000000-0000-0000-0000-000000000000")
        assert "make azure-accelerator-role-apply" in text
        assert "AZURE_ACCELERATOR_LOCATION=eastus" in text
        assert "azure-mgmt-authorization" in text
        assert "| xargs" not in text

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

    def test_rejects_unsafe_instruction_values(self) -> None:
        with pytest.raises(ValueError, match="unsafe subscription_id"):
            azure_onboard.create_role_instructions(subscription_id="sub id; unsafe")


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
    def test_calls_container_apps_list_by_resource_group(self) -> None:
        """Validate probes the exact permission the runtime role actually owns."""
        fake_appcontainers = MagicMock()
        fake_appcontainers.container_apps.list_by_resource_group.return_value = MagicMock(
            next=MagicMock(),  # iterator
        )

        with patch.object(
            azure_onboard,
            "_build_container_apps_client",
            return_value=fake_appcontainers,
        ), \
             patch.object(azure_onboard, "_get_role_assignments", return_value=[]):
            _ok, info = azure_onboard.validate_token_and_role(
                subscription_id=SUBSCRIPTION_ID,
                resource_group_name=RESOURCE_GROUP,
                principal_id="11111111-1111-1111-1111-111111111111",
            )

        fake_appcontainers.container_apps.list_by_resource_group.assert_called_once_with(
            RESOURCE_GROUP,
        )
        assert info["subscription"] == SUBSCRIPTION_ID

    def test_returns_missing_roles_when_empty(self) -> None:
        fake_appcontainers = MagicMock()
        fake_appcontainers.container_apps.list_by_resource_group.return_value = MagicMock(
            next=MagicMock(),
        )

        with patch.object(
            azure_onboard,
            "_build_container_apps_client",
            return_value=fake_appcontainers,
        ), \
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
        fake_appcontainers = MagicMock()
        fake_appcontainers.container_apps.list_by_resource_group.return_value = MagicMock(
            next=MagicMock(),
        )

        all_assignments = [
            {"role_definition_name": r, "principal_id": "principal-1"}
            for r in azure_onboard.EXPECTED_ROLES
        ]
        with patch.object(
            azure_onboard,
            "_build_container_apps_client",
            return_value=fake_appcontainers,
        ), \
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

    def test_requires_subscription_id_when_environment_is_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)

        with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID is unset"):
            azure_onboard.validate_token_and_role(
                principal_id="11111111-1111-1111-1111-111111111111",
            )


class TestAzureSdkBoundaries:
    @staticmethod
    def _sdk_modules() -> tuple[dict[str, ModuleType], MagicMock, MagicMock]:
        azure = ModuleType("azure")
        identity = ModuleType("azure.identity")
        mgmt = ModuleType("azure.mgmt")
        appcontainers = ModuleType("azure.mgmt.appcontainers")
        authorization = ModuleType("azure.mgmt.authorization")
        credential = MagicMock(name="DefaultAzureCredential")
        appcontainers_client = MagicMock(name="ContainerAppsAPIClient")
        identity.__dict__["DefaultAzureCredential"] = credential
        appcontainers.__dict__["ContainerAppsAPIClient"] = appcontainers_client
        return (
            {
                "azure": azure,
                "azure.identity": identity,
                "azure.mgmt": mgmt,
                "azure.mgmt.appcontainers": appcontainers,
                "azure.mgmt.authorization": authorization,
            },
            credential,
            appcontainers_client,
        )

    def test_build_container_apps_client_uses_official_sdk(self) -> None:
        modules, credential, appcontainers_client = self._sdk_modules()
        with patch.dict(sys.modules, modules):
            result = azure_onboard._build_container_apps_client(subscription_id="sub-1")

        assert result is appcontainers_client.return_value
        appcontainers_client.assert_called_once_with(
            credential=credential.return_value,
            subscription_id="sub-1",
        )

    def test_role_assignments_are_normalized(self) -> None:
        modules, credential, _compute_client = self._sdk_modules()
        auth_client_type = MagicMock(name="AuthorizationManagementClient")
        modules["azure.mgmt.authorization"].__dict__["AuthorizationManagementClient"] = (
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

    def test_policy_and_module_cover_only_owned_lifecycle_and_metrics_operations(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        policy = json.loads(AZURE_POLICY_PATH.read_text())

        assert policy["Name"] == ACCELERATOR_ROLE
        assert set(policy["Actions"]) == set(REQUIRED_ACCELERATOR_ACTIONS)
        assert len(REQUIRED_ACCELERATOR_ACTIONS) == 15
        for action in REQUIRED_ACCELERATOR_ACTIONS:
            assert action in main_tf
            assert action in policy["Actions"]

    def test_role_cannot_register_resource_providers(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()

        assert OBSOLETE_PROVIDER_REGISTRATION not in main_tf
        assert "/register/action" not in main_tf

    def test_role_can_target_service_principal_or_managed_identity(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        variables_tf = (AZURE_MODULE_DIR / "variables.tf").read_text()

        assert 'variable "operator_principal_id"' in variables_tf
        assert "var.operator_principal_id" in main_tf
        assert "azurerm_user_assigned_identity.gludd_operator.principal_id" in main_tf

    def test_opa_contract_checks_accelerator_role_resource_group_scope(self) -> None:
        rego_tests = OPA_IAM_TEST_PATH.read_text()
        makefile = (REPO_ROOT / "Makefile").read_text()

        assert "test_azure_accelerator_role_resource_group_scope_passes" in rego_tests
        assert ACCELERATOR_ROLE in rego_tests
        assert '"/subscriptions/sub-123/resourceGroups/gludd-models"' in rego_tests
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


class TestAzureOnboardProvider:
    def test_adapter_forwards_azure_identifiers_and_renders_guides(self) -> None:
        provider = azure_onboard.AzureOnboardProvider(
            subscription_id="sub-123",
            resource_group_name="gludd-accelerator",
            location="westus3",
            identity_name="gludd-operator",
        )
        expected = (True, {"roles_verified": [ACCELERATOR_ROLE]})

        instructions = provider.create_role_instructions()
        assert "sub-123" in instructions
        assert "gludd-accelerator" in instructions
        assert provider.token_acquisition_guide() == azure_onboard.token_acquisition_guide()
        with patch.object(
            azure_onboard,
            "validate_token_and_role",
            return_value=expected,
        ) as validate:
            assert provider.validate_token_and_role(
                token="unused",
                role_arn="principal-123",
                region="unused",
            ) == expected

        validate.assert_called_once_with(
            subscription_id="sub-123",
            resource_group_name="gludd-accelerator",
            principal_id="principal-123",
        )

    def test_adapter_returns_structured_failure(self) -> None:
        provider = azure_onboard.AzureOnboardProvider(subscription_id="sub-123")

        with patch.object(
            azure_onboard,
            "validate_token_and_role",
            side_effect=RuntimeError("sdk unavailable"),
        ):
            ok, info = provider.validate_token_and_role(
                token="unused",
                role_arn="principal-123",
                region="unused",
            )

        assert ok is False
        assert info == {"detail": "RuntimeError: sdk unavailable"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
