"""Terraform contracts for one Gludd-owned Container Apps environment."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra/terraform/modules/azure-container-app-environment"
STACK = ROOT / "infra/terraform/stacks/azure-container-app-environment"

_VARIABLES = {
    "environment_name",
    "resource_group_id",
    "region",
    "workload_profiles",
    "owner_digest",
    "plan_digest",
    "expires_at_utc",
}


def _read(root: Path, name: str) -> str:
    return (root / name).read_text(encoding="utf-8")


def _variable_names(source: str) -> set[str]:
    return set(re.findall(r'^variable "([^"]+)" \{', source, flags=re.MULTILINE))


def test_environment_module_owns_exactly_one_azapi_managed_environment() -> None:
    source = _read(MODULE, "main.tf")

    assert source.count('resource "azapi_resource" "managed_environment"') == 1
    assert 'source = "Azure/azapi"' in source
    assert 'type      = "Microsoft.App/managedEnvironments@2025-07-01"' in source
    assert "parent_id = var.resource_group_id" in source
    assert "location  = var.region" in source
    assert "schema_validation_enabled = true" in source
    assert "workloadProfiles = [" in source
    assert "for profile in var.workload_profiles" in source
    assert "workloadProfileType = profile.workload_profile_type" in source
    assert '"gludd-managed-by"' in source
    assert '"gludd-owner-digest"' in source
    assert '"gludd-plan-digest"' in source
    assert '"gludd-expires-at"' in source
    assert source.count("resource \"") == 1


def test_environment_module_has_no_unrequested_infrastructure_or_secret_surface() -> None:
    rendered = "\n".join(
        _read(MODULE, name) for name in ("main.tf", "variables.tf", "outputs.tf")
    ).casefold()

    for forbidden in (
        "microsoft.network",
        "microsoft.insights",
        "operationalinsights",
        "containerregistr",
        "microsoft.authorization",
        "cognitiveservices",
        "client_secret",
        "shared_key",
        "certificate",
        "vnetconfiguration",
        "applogsconfiguration",
        "minimumcount",
    ):
        assert forbidden not in rendered


def test_module_and_stack_declare_the_same_exact_described_input_contract() -> None:
    for root in (MODULE, STACK):
        source = _read(root, "variables.tf")
        assert _variable_names(source) == _VARIABLES
        assert source.count("description =") == len(_VARIABLES)
        assert "Consumption-GPU-NC8as-T4" in source
        assert "Consumption-GPU-NC24-A100" in source
        assert "length(var.workload_profiles) >= 1" in source
        assert "length(var.workload_profiles) <= 2" in source
        assert "length(distinct(" in source
        assert 'can(regex("^[0-9a-f]{64}$", var.owner_digest))' in source
        assert 'can(regex("^[0-9a-f]{64}$", var.plan_digest))' in source


def test_stack_uses_only_azapi_without_implicit_provider_registration() -> None:
    source = _read(STACK, "main.tf")

    assert 'source  = "Azure/azapi"' in source
    assert 'version = "~> 2.0"' in source
    assert "skip_provider_registration = true" in source
    assert source.count('module "environment"') == 1
    assert 'source = "../../modules/azure-container-app-environment"' in source
    for name in sorted(_VARIABLES):
        assert f"{name} = var.{name}" in source
    assert "azurerm" not in source.casefold()


def test_outputs_expose_only_identity_and_exact_cleanup_boundary() -> None:
    module_outputs = _read(MODULE, "outputs.tf")
    stack_outputs = _read(STACK, "outputs.tf")

    assert set(re.findall(r'^output "([^"]+)"', module_outputs, re.MULTILINE)) == {
        "environment_id",
        "cleanup_boundary",
    }
    assert set(re.findall(r'^output "([^"]+)"', stack_outputs, re.MULTILINE)) == {
        "environment_id",
        "cleanup_boundary",
        "runtime_class",
    }
    assert "azapi_resource.managed_environment.id" in module_outputs
    assert "module.environment.environment_id" in stack_outputs
    assert "module.environment.cleanup_boundary" in stack_outputs
    assert module_outputs.count("description =") == 2
    assert 'value       = "control-plane"' in stack_outputs
    assert stack_outputs.count("description =") == 3


def test_environment_assets_do_not_claim_to_create_a_shared_operator_resource() -> None:
    rendered = "\n".join(
        _read(root, name)
        for root in (MODULE, STACK)
        for name in ("main.tf", "variables.tf", "outputs.tf")
    ).casefold()

    assert "operator-owned" not in rendered
    assert "shared environment" not in rendered
    assert "pre-created" not in rendered
    assert "preexisting environment" not in rendered


def test_state_free_terraform_validation_target_accepts_environment_stack() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.partition("tf-init-local: tf-cache-setup")[2].partition(
        "# Validates a single stack"
    )[0]

    assert "stacks/azure-container-app-environment" in target
    assert "TF_INIT_LOCAL_VALIDATE_ONLY" in target
    assert "terraform init -backend=false" in target
    assert "terraform validate" in target
    assert target.index("terraform init -backend=false") < target.index(
        "terraform validate"
    )
