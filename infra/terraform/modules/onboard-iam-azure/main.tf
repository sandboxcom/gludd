# Azure onboarding IAM module — provisions the least-privilege user-assigned
# managed identity gludd uses to launch and tear down ephemeral GPU compute.
#
# The custom role below is the minimal control-plane surface required by the
# release stack: read SKU/quota availability, create the network and VM, install
# the NVIDIA GPU extension, and tear every resource back down.  Asserted by
# tests/unit/test_onboard_azure.py::TestTerraformModuleLeastPriv.

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.55"
    }
  }
}

provider "azurerm" {
  features {}
}

# gludd needs a resource group to land the identity (and, later, its VMs).
resource "azurerm_resource_group" "gludd_rg" {
  name     = var.resource_group_name
  location = var.location
}

# The gludd operator user-assigned managed identity.
# Default name: gludd-compute-operator (overridable via var.identity_name).
resource "azurerm_user_assigned_identity" "gludd_operator" {
  name                = var.identity_name
  resource_group_name = azurerm_resource_group.gludd_rg.name
  location            = azurerm_resource_group.gludd_rg.location
}

locals {
  subscription_scope = "/subscriptions/${var.subscription_id}"
  operator_principal_id = coalesce(
    var.operator_principal_id,
    azurerm_user_assigned_identity.gludd_operator.principal_id,
  )
}

# Do not replace this with Owner or Contributor.  The explicit action list is
# intentionally duplicated in config/infra/azure-iam-policy.json so operators
# can provision the same role outside Terraform.
resource "azurerm_role_definition" "accelerator_deployer" {
  name        = "General Ludd Accelerator Deployer"
  scope       = local.subscription_scope
  description = "Deploy, validate, monitor, and destroy ephemeral Azure GPU workers for gludd."

  permissions {
    actions = [
      "Microsoft.Resources/subscriptions/resourceGroups/read",
      "Microsoft.Resources/subscriptions/resourceGroups/write",
      "Microsoft.Resources/subscriptions/resourceGroups/delete",
      "Microsoft.Resources/subscriptions/locations/read",
      "Microsoft.Resources/subscriptions/providers/read",
      "Microsoft.Resources/subscriptions/providers/register/action",
      "Microsoft.Compute/skus/read",
      "Microsoft.Compute/locations/usages/read",
      "Microsoft.Compute/locations/vmSizes/read",
      "Microsoft.Compute/virtualMachines/read",
      "Microsoft.Compute/virtualMachines/write",
      "Microsoft.Compute/virtualMachines/delete",
      "Microsoft.Compute/virtualMachines/start/action",
      "Microsoft.Compute/virtualMachines/restart/action",
      "Microsoft.Compute/virtualMachines/deallocate/action",
      "Microsoft.Compute/virtualMachines/instanceView/read",
      "Microsoft.Compute/virtualMachines/extensions/read",
      "Microsoft.Compute/virtualMachines/extensions/write",
      "Microsoft.Compute/virtualMachines/extensions/delete",
      "Microsoft.Compute/disks/read",
      "Microsoft.Compute/disks/write",
      "Microsoft.Compute/disks/delete",
      "Microsoft.Network/virtualNetworks/read",
      "Microsoft.Network/virtualNetworks/write",
      "Microsoft.Network/virtualNetworks/delete",
      "Microsoft.Network/virtualNetworks/subnets/read",
      "Microsoft.Network/virtualNetworks/subnets/write",
      "Microsoft.Network/virtualNetworks/subnets/delete",
      "Microsoft.Network/virtualNetworks/subnets/join/action",
      "Microsoft.Network/networkSecurityGroups/read",
      "Microsoft.Network/networkSecurityGroups/write",
      "Microsoft.Network/networkSecurityGroups/delete",
      "Microsoft.Network/networkSecurityGroups/securityRules/read",
      "Microsoft.Network/networkSecurityGroups/securityRules/write",
      "Microsoft.Network/networkSecurityGroups/securityRules/delete",
      "Microsoft.Network/publicIPAddresses/read",
      "Microsoft.Network/publicIPAddresses/write",
      "Microsoft.Network/publicIPAddresses/delete",
      "Microsoft.Network/publicIPAddresses/join/action",
      "Microsoft.Network/networkInterfaces/read",
      "Microsoft.Network/networkInterfaces/write",
      "Microsoft.Network/networkInterfaces/delete",
      "Microsoft.Network/networkInterfaces/join/action",
      "Microsoft.Authorization/roleAssignments/read",
      "Microsoft.Authorization/roleDefinitions/read",
    ]
    not_actions = [
      "Microsoft.Authorization/roleAssignments/write",
      "Microsoft.Authorization/roleAssignments/delete",
      "Microsoft.Authorization/roleDefinitions/write",
      "Microsoft.Authorization/roleDefinitions/delete",
      "Microsoft.Compute/virtualMachines/runCommand/action",
      "Microsoft.Compute/virtualMachines/runCommands/read",
      "Microsoft.Compute/virtualMachines/runCommands/write",
      "Microsoft.Compute/virtualMachines/runCommands/delete",
    ]
  }

  assignable_scopes = [local.subscription_scope]
}

# By default the role belongs to the managed identity above.  Supplying an
# app/service-principal object id makes the exact same role usable by gludd
# running outside Azure with AZURE_* service-principal credentials.
resource "azurerm_role_assignment" "accelerator_deployer" {
  scope                            = local.subscription_scope
  role_definition_id              = azurerm_role_definition.accelerator_deployer.role_definition_resource_id
  principal_id                     = local.operator_principal_id
  skip_service_principal_aad_check = true
}
