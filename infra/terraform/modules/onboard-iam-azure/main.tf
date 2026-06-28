# Azure onboarding IAM module — provisions the least-privilege user-assigned
# managed identity gludd uses to launch and tear down ephemeral GPU compute.
#
# Role set is the minimal set that
# src/general_ludd/infra/terraform.py::_generate_azure requires to materialise
# its plan (azurerm_virtual_machine + supporting RG/vnet/NSG/NIC/IP). Asserted
# by tests/unit/test_onboard_azure.py::TestTerraformModuleLeastPriv.

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
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

# Create + manage VMs (azurerm_virtual_machine in _generate_azure). This is
# the scoped built-in; the broader all-resource role is intentionally NOT used.
resource "azurerm_role_assignment" "vm_contributor" {
  scope                            = "/subscriptions/${var.subscription_id}"
  role_definition_name             = "Virtual Machine Contributor"
  principal_id                     = azurerm_user_assigned_identity.gludd_operator.principal_id
  skip_service_principal_aad_check = true
}

# Allow the operator to assign its own identity to the VMs it creates.
resource "azurerm_role_assignment" "managed_identity_operator" {
  scope                            = "/subscriptions/${var.subscription_id}"
  role_definition_name             = "Managed Identity Operator"
  principal_id                     = azurerm_user_assigned_identity.gludd_operator.principal_id
  skip_service_principal_aad_check = true
}
