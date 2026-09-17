"""Structural proof that the Azure GPU stack owns only one bounded app."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra/terraform/modules/azure-container-app-vllm"
STACK = ROOT / "infra/terraform/stacks/azure-container-app-vllm"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_module_owns_only_the_container_app_in_existing_infrastructure() -> None:
    main = _read(MODULE / "main.tf")

    assert 'resource "azapi_resource" "vllm"' in main
    assert len(re.findall(r'^resource\s+"', main, re.MULTILINE)) == 1
    assert 'resource "azurerm_resource_group"' not in main
    assert 'resource "azapi_resource" "gludd_environment"' not in main
    assert 'resource "azurerm_container_app"' not in main
    assert "parent_id = var.resource_group_id" in main
    assert "managedEnvironmentId = var.managed_environment_id" in main


def test_module_uses_only_azapi_and_disables_provider_registration() -> None:
    module = _read(MODULE / "main.tf")
    stack = _read(STACK / "main.tf")

    assert 'source = "Azure/azapi"' in module
    assert "hashicorp/azurerm" not in module
    assert "hashicorp/azurerm" not in stack
    assert 'provider "azapi"' in stack
    assert "skip_provider_registration = true" in stack
    assert 'module "gpu_cost_watchdog"' in stack
    assert 'cloud           = "azure"' in stack


def test_model_and_image_provenance_are_immutable_and_forwarded() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert "@sha256:" in variables
    assert "container_image must be digest-pinned" in variables
    assert "model_revision must be a 40-character commit SHA" in variables
    assert '["--model", var.model_name, "--revision", var.model_revision' in main
    assert 'name  = "HF_HOME"' in main
    assert 'value = "/tmp/huggingface"' in main
    assert 'name  = "HF_HUB_DISABLE_TELEMETRY"' in main


def test_ready_revision_requires_cuda_kernel_canary_before_vllm() -> None:
    main = _read(MODULE / "main.tf")

    assert "cuda_startup_canary = <<-PY" in main
    assert "torch.cuda.is_available()" in main
    assert "torch.cuda.device_count() != 1" in main
    assert "torch.mm(left, left)" in main
    assert "torch.cuda.synchronize()" in main
    assert 'os.execvp("vllm", ["vllm", "serve", *sys.argv[1:]])' in main
    assert 'command = ["python3", "-c", local.cuda_startup_canary]' in main


def test_t4_and_a100_are_not_conflated_and_app_is_bounded() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert "a100_40" not in main
    assert 'contains(["t4", "a100_80"], var.gpu_type)' in variables
    assert 'workload_profile_type = "Consumption-GPU-NC8as-T4"' in main
    assert 'workload_profile_type = "Consumption-GPU-NC24-A100"' in main
    assert "minReplicas = var.min_replicas" in main
    assert "maxReplicas = var.max_replicas" in main
    assert "concurrentRequests = tostring(var.http_concurrent_requests)" in main
    assert "ipSecurityRestrictions" in main
    assert "var.allowed_cidr" in main
    assert "allowed_cidr must be one IPv4 /32" in variables


def test_cost_cleanup_and_ownership_inputs_fail_closed() -> None:
    variables = _read(MODULE / "variables.tf")
    stack_variables = _read(STACK / "variables.tf")
    main = _read(MODULE / "main.tf")

    for text in (variables, stack_variables):
        assert "max_cost_usd <= 5" in text
        assert "timeout_minutes <= 60" in text
        assert 'variable "expires_at_utc"' in text
        assert 'variable "owner_token"' in text
        assert 'variable "trace_id"' in text
    assert "gludd-expires-at" in main
    assert "gludd-owner" in main
    assert "gludd-trace-id" in main
    assert "prevent_destroy" not in main


def test_stack_forwards_exact_existing_resource_and_revision_inputs() -> None:
    main = _read(STACK / "main.tf")

    for assignment in (
        "resource_group_id              = var.resource_group_id",
        "managed_environment_id         = var.managed_environment_id",
        "workload_profile_name          = var.workload_profile_name",
        "model_revision                 = var.model_revision",
        "expires_at_utc                 = var.expires_at_utc",
        "owner_token                    = var.owner_token",
        "trace_id                       = var.trace_id",
    ):
        assert assignment in main


def test_outputs_describe_app_only_cleanup_boundary() -> None:
    module_outputs = _read(MODULE / "outputs.tf")
    stack_outputs = _read(STACK / "outputs.tf")

    assert "azapi_resource.vllm.id" in module_outputs
    assert "azapi_resource.vllm.output.properties.configuration.ingress.fqdn" in module_outputs
    assert "azapi_resource.vllm.output.properties.latestReadyRevisionName" in module_outputs
    assert 'output "revision_name"' in module_outputs
    assert 'output "revision_name"' in stack_outputs
    assert "module.vllm_server.revision_name" in stack_outputs
    assert "existing resource group; never deleted" in module_outputs
    assert 'output "watchdog_user_data"' in stack_outputs
    assert "module.gpu_cost_watchdog.user_data" in stack_outputs
    assert "resource_group_name" not in stack_outputs
