"""Unit tests for the GCP onboarding provider (`gludd onboard gcp`)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.onboard import gcp as gcp_onboard

REPO_ROOT = Path(__file__).resolve().parents[2]
GCP_MODULE_DIR = REPO_ROOT / "infra" / "terraform" / "modules" / "onboard-iam-gcp"


# ---------------------------------------------------------------------------
# create_role_instructions
# ---------------------------------------------------------------------------

class TestCreateRoleInstructions:
    def test_mentions_terraform_apply(self) -> None:
        text = gcp_onboard.create_role_instructions(project_id="my-project-123")
        assert "terraform init" in text.lower()
        assert "terraform apply" in text.lower()

    def test_mentions_service_account_email(self) -> None:
        text = gcp_onboard.create_role_instructions(project_id="my-project-123")
        # The default SA name is gludd-compute-operator; instructions should
        # reference the service-account email shape.
        assert "service account" in text.lower()
        assert "gludd-compute-operator" in text
        assert "@" in text  # email-shaped reference

    def test_mentions_gcloud_auth_and_project(self) -> None:
        text = gcp_onboard.create_role_instructions(project_id="my-project-123")
        assert "gcloud auth login" in text
        assert "gcloud config set project" in text

    def test_mentions_module_path(self) -> None:
        text = gcp_onboard.create_role_instructions(project_id="my-project-123")
        assert "onboard-iam-gcp" in text


# ---------------------------------------------------------------------------
# token_acquisition_guide
# ---------------------------------------------------------------------------

class TestTokenAcquisitionGuide:
    def test_mentions_GOOGLE_APPLICATION_CREDENTIALS(self) -> None:
        text = gcp_onboard.token_acquisition_guide()
        assert "GOOGLE_APPLICATION_CREDENTIALS" in text

    def test_mentions_key_creation_command(self) -> None:
        text = gcp_onboard.token_acquisition_guide()
        # Either gcloud keys create path OR console guidance.
        assert "gcloud iam service-accounts keys create" in text or "console" in text.lower()

    def test_mentions_json_key(self) -> None:
        text = gcp_onboard.token_acquisition_guide()
        assert "json" in text.lower()


# ---------------------------------------------------------------------------
# validate_token_and_role
# ---------------------------------------------------------------------------

class TestValidateTokenAndRole:
    def test_calls_compute_instances_list(self, tmp_path: Path) -> None:
        """Validate probes the compute.instances.list API on the target project."""
        fake_key = tmp_path / "key.json"
        fake_key.write_text(json.dumps({
            "type": "service_account",
            "project_id": "proj-123",
            "client_email": "gludd-compute-operator@proj-123.iam.gserviceaccount.com",
        }))

        fake_discovery = MagicMock()
        fake_compute = MagicMock()
        fake_instances = MagicMock()
        fake_instances.list = MagicMock(return_value=MagicMock(execute=MagicMock(return_value={"items": []})))
        fake_compute.instances.return_value = fake_instances
        fake_discovery.build.return_value = fake_compute

        with patch.object(gcp_onboard, "_build_gcp_client", return_value=fake_discovery), \
             patch.object(gcp_onboard, "_get_iam_policy", return_value={"bindings": []}):
            _ok, info = gcp_onboard.validate_token_and_role(
                token_path=str(fake_key),
                project_id="proj-123",
            )

        fake_instances.list.assert_called_once()
        # The probe must target the project we were asked to validate.
        _args, kwargs = fake_instances.list.call_args
        assert kwargs.get("project") == "proj-123" or "proj-123" in str(_args)
        assert info["project_id"] == "proj-123"

    def test_returns_missing_roles(self, tmp_path: Path) -> None:
        fake_key = tmp_path / "key.json"
        fake_key.write_text(json.dumps({"type": "service_account"}))

        fake_discovery = MagicMock()
        fake_compute = MagicMock()
        fake_compute.instances.list.return_value.execute.return_value = {"items": []}
        fake_discovery.build.return_value = fake_compute

        # Policy that grants NOTHING to the SA → all expected roles missing.
        with patch.object(gcp_onboard, "_build_gcp_client", return_value=fake_discovery), \
             patch.object(gcp_onboard, "_get_iam_policy", return_value={"bindings": []}):
            ok, info = gcp_onboard.validate_token_and_role(
                token_path=str(fake_key),
                project_id="proj-123",
                service_account_email="gludd-compute-operator@proj-123.iam.gserviceaccount.com",
            )

        assert ok is False
        assert isinstance(info["missing"], list)
        assert len(info["missing"]) > 0
        # Roles gludd needs:
        for role in info["missing"]:
            assert role.startswith("roles/")

    def test_ok_when_all_roles_present(self, tmp_path: Path) -> None:
        fake_key = tmp_path / "key.json"
        fake_key.write_text(json.dumps({"type": "service_account"}))

        sa_email = "gludd-compute-operator@proj-123.iam.gserviceaccount.com"
        fake_discovery = MagicMock()
        fake_compute = MagicMock()
        fake_compute.instances.list.return_value.execute.return_value = {"items": []}
        fake_discovery.build.return_value = fake_compute

        policy = {
            "bindings": [
                {"role": r, "members": [f"serviceAccount:{sa_email}"]}
                for r in gcp_onboard.EXPECTED_ROLES
            ]
        }
        with patch.object(gcp_onboard, "_build_gcp_client", return_value=fake_discovery), \
             patch.object(gcp_onboard, "_get_iam_policy", return_value=policy):
            ok, info = gcp_onboard.validate_token_and_role(
                token_path=str(fake_key),
                project_id="proj-123",
                service_account_email=sa_email,
            )

        assert ok is True
        assert info["missing"] == []
        assert set(info["roles_verified"]) == set(gcp_onboard.EXPECTED_ROLES)


# ---------------------------------------------------------------------------
# Terraform module — least-privilege IAM policy
# ---------------------------------------------------------------------------

class TestTerraformModuleLeastPriv:
    def test_module_files_exist(self) -> None:
        assert GCP_MODULE_DIR.is_dir(), f"Missing module dir: {GCP_MODULE_DIR}"
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            assert (GCP_MODULE_DIR / name).is_file(), f"Missing {name}"

    def test_iam_policy_uses_least_priv_roles(self) -> None:
        main_tf = (GCP_MODULE_DIR / "main.tf").read_text()
        # Every EXPECTED role must appear in the module.
        for role in gcp_onboard.EXPECTED_ROLES:
            assert role in main_tf, f"Expected role {role} missing from main.tf"

        # Forbidden broad roles.
        forbidden = ["roles/owner", "roles/editor", "roles/compute.admin"]
        for bad in forbidden:
            assert bad not in main_tf, f"Forbidden broad role {bad} present in main.tf"

    def test_creates_service_account(self) -> None:
        main_tf = (GCP_MODULE_DIR / "main.tf").read_text()
        assert "google_service_account" in main_tf
        assert "gludd-compute-operator" in main_tf

    def test_outputs_service_account_email(self) -> None:
        outputs_tf = (GCP_MODULE_DIR / "outputs.tf").read_text()
        assert "service_account_email" in outputs_tf

    def test_outputs_mark_sensitive_when_key_emitted(self) -> None:
        outputs_tf = (GCP_MODULE_DIR / "outputs.tf").read_text()
        # If the module emits a key at all, it must be marked sensitive.
        if "service_account_key" in outputs_tf or "private_key" in outputs_tf:
            assert "sensitive = true" in outputs_tf or "sensitive" in outputs_tf


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
