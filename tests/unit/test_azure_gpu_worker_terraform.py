"""Contracts for the bounded Azure single-GPU-VM provisioning slice."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra" / "terraform" / "modules" / "azure-gpu-worker"
POLICY = ROOT / "config" / "infra" / "azure-gpu-worker-iam-policy.json"
README = MODULE / "README.md"

EXPECTED_RESOURCES = {
    "azurerm_linux_virtual_machine",
    "azurerm_network_interface",
    "azurerm_network_interface_security_group_association",
    "azurerm_network_security_group",
    "azurerm_public_ip",
    "azurerm_subnet",
    "azurerm_virtual_network",
}

EXPECTED_ACTIONS = {
    "Microsoft.Compute/disks/delete",
    "Microsoft.Compute/disks/read",
    "Microsoft.Compute/disks/write",
    "Microsoft.Compute/virtualMachines/delete",
    "Microsoft.Compute/virtualMachines/instanceView/read",
    "Microsoft.Compute/virtualMachines/read",
    "Microsoft.Compute/virtualMachines/write",
    "Microsoft.Insights/metrics/read",
    "Microsoft.Network/networkInterfaces/delete",
    "Microsoft.Network/networkInterfaces/join/action",
    "Microsoft.Network/networkInterfaces/read",
    "Microsoft.Network/networkInterfaces/write",
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


def test_module_has_complete_open_tofu_contract() -> None:
    assert MODULE.is_dir()
    for filename in ("main.tf", "variables.tf", "outputs.tf", "README.md"):
        assert (MODULE / filename).is_file(), filename

    main = _read(MODULE / "main.tf")
    resources = set(re.findall(r'^resource\s+"([^"]+)"\s+"', main, re.MULTILINE))
    assert resources == EXPECTED_RESOURCES
    assert 'source  = "hashicorp/azurerm"' in main
    assert 'version = "~> 4.55"' in main


def test_module_never_owns_group_iam_guest_commands_or_scale_sets() -> None:
    text = _module_text()
    forbidden = (
        "azurerm_resource_group",
        "azurerm_role_assignment",
        "azurerm_role_definition",
        "azurerm_virtual_machine_extension",
        "azurerm_linux_virtual_machine_scale_set",
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


def test_vm_shape_and_accelerated_networking_are_planner_inputs() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert re.search(r"^\s*size\s*=\s*var\.vm_size$", main, re.MULTILINE)
    assert "accelerated_networking_enabled = var.accelerated_networking_enabled" in main
    assert 'variable "vm_size"' in variables
    assert 'startswith(var.vm_size, "Standard_")' in variables
    assert "Standard_NC" not in variables
    assert "Standard_ND" not in variables
    assert "Standard_NV" not in variables
    assert "contains([" not in variables


def test_network_and_login_are_controller_bounded_and_key_only() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert main.count("source_address_prefix      = var.controller_cidr") == 2
    assert 'destination_port_range     = "22"' in main
    assert "destination_port_range     = tostring(var.inference_port)" in main
    assert "disable_password_authentication = true" in main
    assert "public_key = var.ssh_public_key" in main
    assert "file(" not in main
    assert "endswith(var.controller_cidr, \"/32\")" in variables
    assert "0.0.0.0/0" not in _module_text()


def test_vm_image_is_immutable_and_spot_lifecycle_is_explicit() -> None:
    main = _read(MODULE / "main.tf")
    variables = _read(MODULE / "variables.tf")

    assert "version   = var.image_version" in main
    assert 'lower(var.image_version) != "latest"' in variables
    assert re.search(
        r'^\s*priority\s*=\s*var\.use_spot \? "Spot" : "Regular"$',
        main,
        re.MULTILINE,
    )
    assert re.search(
        r'^\s*eviction_policy\s*=\s*var\.use_spot \? "Delete" : null$',
        main,
        re.MULTILINE,
    )
    assert re.search(
        r"^\s*max_bid_price\s*=\s*var\.use_spot \? var\.max_spot_price : null$",
        main,
        re.MULTILINE,
    )
    assert "prevent_destroy" not in main


def test_owned_resources_are_correlated_and_expire() -> None:
    main = _read(MODULE / "main.tf")
    outputs = _read(MODULE / "outputs.tf")

    for tag in (
        "managed-by",
        "gludd-owner",
        "gludd-trace-id",
        "gludd-expires-at",
        "gludd-lifecycle",
        "accelerator-sku",
    ):
        assert tag in main
    assert 'output "owned_resource_ids"' in outputs
    for resource in EXPECTED_RESOURCES:
        assert resource in outputs
    assert "resource_group_id" not in outputs


def test_policy_is_exact_resource_group_scope_and_matches_resources() -> None:
    policy = json.loads(_read(POLICY))

    assert policy["Name"] == "General Ludd GPU Worker Deployer"
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
        "Microsoft.Compute/virtualMachines/runCommands/",
        "Microsoft.Compute/virtualMachines/extensions/",
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


def test_documentation_records_provider_and_long_lived_user_findings() -> None:
    text = _read(README)

    assert "OpenTofu" in text
    assert "Ansible" in text
    assert "exact resource group" in text.lower()
    assert "https://registry.terraform.io/providers/hashicorp/azurerm" in text
    assert "https://github.com/hashicorp/terraform-provider-azurerm/issues/23488" in text
    assert "https://github.com/hashicorp/terraform-provider-azurerm/issues/9344" in text
    assert "https://github.com/hashicorp/terraform-provider-azurerm/issues/23688" in text
    assert 'resource_provider_registrations = "none"' in text
    assert "state" in text.lower()
    assert "extension" in text.lower()
