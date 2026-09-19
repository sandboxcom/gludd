"""Contracts for the bounded Azure Uniform GPU VMSS provisioning slice."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra" / "terraform" / "modules" / "azure-gpu-vmss-worker"
POLICY = ROOT / "config" / "infra" / "azure-gpu-vmss-worker-iam-policy.json"
README = MODULE / "README.md"

EXPECTED_RESOURCES = {
    "azurerm_linux_virtual_machine_scale_set",
    "azurerm_nat_gateway",
    "azurerm_nat_gateway_public_ip_association",
    "azurerm_network_security_group",
    "azurerm_public_ip",
    "azurerm_subnet",
    "azurerm_subnet_nat_gateway_association",
    "azurerm_subnet_network_security_group_association",
    "azurerm_virtual_network",
}

EXPECTED_ACTIONS = {
    "Microsoft.Compute/disks/delete",
    "Microsoft.Compute/disks/read",
    "Microsoft.Compute/disks/write",
    "Microsoft.Compute/virtualMachineScaleSets/delete",
    "Microsoft.Compute/virtualMachineScaleSets/extensions/delete",
    "Microsoft.Compute/virtualMachineScaleSets/extensions/read",
    "Microsoft.Compute/virtualMachineScaleSets/extensions/write",
    "Microsoft.Compute/virtualMachineScaleSets/read",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/instanceView/read",
    "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/read",
    "Microsoft.Compute/virtualMachineScaleSets/write",
    "Microsoft.Insights/metrics/read",
    "Microsoft.ManagedIdentity/userAssignedIdentities/assign/action",
    "Microsoft.ManagedIdentity/userAssignedIdentities/read",
    "Microsoft.Network/natGateways/delete",
    "Microsoft.Network/natGateways/join/action",
    "Microsoft.Network/natGateways/read",
    "Microsoft.Network/natGateways/write",
    "Microsoft.Network/networkSecurityGroups/delete",
    "Microsoft.Network/networkSecurityGroups/join/action",
    "Microsoft.Network/networkSecurityGroups/read",
    "Microsoft.Network/networkSecurityGroups/securityRules/delete",
    "Microsoft.Network/networkSecurityGroups/securityRules/read",
    "Microsoft.Network/networkSecurityGroups/securityRules/write",
    "Microsoft.Network/networkSecurityGroups/write",
    "Microsoft.Network/publicIPAddresses/delete",
    "Microsoft.Network/publicIPAddresses/join/action",
    "Microsoft.Network/publicIPAddresses/read",
    "Microsoft.Network/publicIPAddresses/write",
    "Microsoft.Network/virtualNetworks/delete",
    "Microsoft.Network/virtualNetworks/read",
    "Microsoft.Network/virtualNetworks/subnets/delete",
    "Microsoft.Network/virtualNetworks/subnets/join/action",
    "Microsoft.Network/virtualNetworks/subnets/read",
    "Microsoft.Network/virtualNetworks/subnets/write",
    "Microsoft.Network/virtualNetworks/write",
    "Microsoft.Resources/subscriptions/resourceGroups/read",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module_text() -> str:
    return "\n".join(
        _read(MODULE / name) for name in ("main.tf", "variables.tf", "outputs.tf")
    )


def test_module_has_complete_uniform_vmss_contract() -> None:
    assert MODULE.is_dir()
    for filename in ("main.tf", "variables.tf", "outputs.tf", "README.md"):
        assert (MODULE / filename).is_file(), filename

    main = _read(MODULE / "main.tf")
    resources = set(re.findall(r'^resource\s+"([^"]+)"\s+"', main, re.MULTILINE))
    assert resources == EXPECTED_RESOURCES
    assert 'source  = "hashicorp/azurerm"' in main
    assert 'version = "~> 4.55"' in main


def test_module_never_owns_group_iam_or_guest_bootstrap() -> None:
    text = _module_text()
    forbidden = (
        "azurerm_resource_group",
        "azurerm_role_assignment",
        "azurerm_role_definition",
        "azurerm_orchestrated_virtual_machine_scale_set",
        "azurerm_virtual_machine_scale_set_extension",
        "custom_data",
        "CustomScript",
        "runCommand",
        "runCommands",
        'provisioner "',
        "private_key",
        "admin_password",
        "client_secret",
    )
    for token in forbidden:
        assert token not in text, token

    assert "resource_group_name = var.resource_group_name" in text
    assert "resource_group_id" in text
    assert "existing resource-group ID" in text


def test_capacity_shape_and_networking_come_from_live_planner_evidence() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert re.search(r"^\s*sku\s*=\s*var\.vm_size$", main, re.MULTILINE)
    assert re.search(r"^\s*instances\s*=\s*var\.instance_count$", main, re.MULTILINE)
    assert re.search(
        r"^\s*zones\s*=\s*var\.availability_zones$", main, re.MULTILINE
    )
    assert re.search(
        r"^\s*enable_accelerated_networking\s*=\s*"
        r"var\.accelerated_networking_enabled$",
        main,
        re.MULTILINE,
    )
    assert 'variable "vm_size"' in variables
    assert 'startswith(var.vm_size, "Standard_")' in variables
    assert "Standard_NC" not in variables
    assert "Standard_ND" not in variables
    assert "Standard_NV" not in variables
    assert "contains([" not in variables


def test_network_is_private_controller_bounded_and_has_explicit_egress() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert main.count("source_address_prefix      = var.controller_cidr") == 2
    assert 'destination_port_range     = "22"' in main
    assert "destination_port_range     = tostring(var.inference_port)" in main
    assert "disable_password_authentication = true" in main
    assert "public_key = var.ssh_public_key" in main
    assert "azurerm_subnet_nat_gateway_association" in main
    assert "azurerm_nat_gateway_public_ip_association" in main
    assert "public_ip_address {" not in main
    assert "endswith(var.controller_cidr, \"/32\")" in variables
    assert "0.0.0.0/0" not in _module_text()


def test_vmss_uses_only_user_assigned_identity_and_immutable_inputs() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert 'type         = "UserAssigned"' in main
    assert "identity_ids = [var.user_assigned_identity_id]" in main
    assert "version   = var.image_version" in main
    assert 'lower(var.image_version) != "latest"' in variables
    assert re.search(
        r'^\s*priority\s*=\s*var\.use_spot \? "Spot" : "Regular"$',
        main,
        re.MULTILINE,
    )
    assert re.search(r"^\s*overprovision\s*=\s*false$", main, re.MULTILINE)
    assert "prevent_destroy" not in main


def test_uniform_rdma_and_zdd_health_contracts_are_explicit() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert re.search(
        r"^\s*single_placement_group\s*=\s*var\.rdma_enabled \? true",
        main,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*platform_fault_domain_count\s*=\s*"
        r"var\.platform_fault_domain_count$",
        main,
        re.MULTILINE,
    )
    assert re.search(r'^\s*upgrade_mode\s*=\s*"Rolling"$', main, re.MULTILINE)
    assert "rolling_upgrade_policy" in main
    assert "max_batch_instance_percent              = var.max_batch_percent" in main
    assert "automatic_instance_repair" in main
    assert "grace_period = var.repair_grace_period" in main
    assert 'publisher                  = "Microsoft.ManagedServices"' in main
    assert 'type                       = "ApplicationHealthLinux"' in main
    assert 'type_handler_version       = "2.0"' in main
    assert "requestPath       = var.health_path" in main
    assert 'default     = "/healthz"' in variables
    assert "health_probe_id" not in main


def test_optional_cache_requires_independent_price_attestation() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert 'dynamic "data_disk"' in main
    assert "var.cache_disk_enabled ? [1] : []" in main
    assert "storage_account_type = var.cache_disk_storage_account_type" in main
    assert "disk_size_gb         = var.cache_disk_size_gb" in main
    assert "!var.cache_disk_enabled || var.cache_price_attested" in main
    assert 'variable "cache_price_attested"' in variables


def test_outputs_expose_cleanup_and_dynamic_inventory_boundaries() -> None:
    outputs = _read(MODULE / "outputs.tf")

    assert 'output "virtual_machine_scale_set_id"' in outputs
    assert 'output "private_instance_inventory_query"' in outputs
    assert 'output "owned_resource_ids"' in outputs
    for resource in EXPECTED_RESOURCES:
        assert resource in outputs
    assert "resource_group_id" not in outputs


def test_policy_is_exact_resource_group_scope_and_matches_resources() -> None:
    policy = json.loads(_read(POLICY))

    assert policy["Name"] == "General Ludd GPU VMSS Worker Deployer"
    assert set(policy["Actions"]) == EXPECTED_ACTIONS
    assert policy["NotActions"] == []
    assert policy["DataActions"] == []
    assert policy["NotDataActions"] == []
    assert policy["AssignableScopes"] == [
        "/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    ]


@pytest.mark.parametrize(
    "forbidden",
    [
        "*",
        "Microsoft.Authorization/",
        "Microsoft.Resources/subscriptions/resourceGroups/write",
        "Microsoft.Resources/subscriptions/resourceGroups/delete",
        "Microsoft.Compute/virtualMachines/runCommand/action",
        "Microsoft.Compute/virtualMachineScaleSets/virtualMachines/runCommand/",
        "Microsoft.KeyVault/",
        "Microsoft.ContainerRegistry/",
        "/register/action",
    ],
)
def test_policy_excludes_privilege_and_guest_execution(forbidden: str) -> None:
    actions = json.loads(_read(POLICY))["Actions"]
    if forbidden == "*":
        assert all("*" not in action for action in actions)
    else:
        assert all(forbidden.casefold() not in action.casefold() for action in actions)


def test_documentation_records_mode_limits_and_long_lived_user_findings() -> None:
    text = _read(README)

    assert "Uniform" in text
    assert "InfiniBand" in text
    assert "Ansible" in text
    assert "OpenBao" in text
    assert "EC2" in text
    assert "TPU" in text
    assert "exact resource group" in text.lower()
    assert "https://registry.terraform.io/providers/hashicorp/azurerm" in text
    assert "virtual-machine-scale-sets-orchestration-modes" in text
    assert "virtual-machine-scale-sets-health-extension" in text
    assert "questions/5692668" in text
    assert "questions/2338751" in text
    assert "resource_provider_registrations = \"none\"" in text
