"""Unit tests for the Azure onboarding provider (`gludd onboard azure`)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.onboard import azure as azure_onboard

REPO_ROOT = Path(__file__).resolve().parents[2]
AZURE_MODULE_DIR = REPO_ROOT / "infra" / "terraform" / "modules" / "onboard-iam-azure"


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


# ---------------------------------------------------------------------------
# Terraform module — least-privilege IAM policy
# ---------------------------------------------------------------------------

class TestTerraformModuleLeastPriv:
    def test_module_files_exist(self) -> None:
        assert AZURE_MODULE_DIR.is_dir(), f"Missing module dir: {AZURE_MODULE_DIR}"
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            assert (AZURE_MODULE_DIR / name).is_file(), f"Missing {name}"

    def test_iam_policy_uses_vm_contributor_role(self) -> None:
        main_tf = (AZURE_MODULE_DIR / "main.tf").read_text()
        assert "Virtual Machine Contributor" in main_tf
        assert "Managed Identity Operator" in main_tf

        # Forbidden broad roles.
        for bad in ('"Contributor"', '"Owner"'):
            assert bad not in main_tf, f"Forbidden broad built-in role {bad} present in main.tf"

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
