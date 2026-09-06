# Azure onboarding IAM module — provisions the least-privilege user-assigned
# managed identity gludd uses for one pre-created Container Apps environment.
#
# The custom role below is the minimal control-plane surface required by the
# release stack: inspect the environment quota, deploy one Container App, inspect
# its revision, and tear that app back down. Environment/bootstrap administration
# remains with the human operator. Asserted by
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
  resource_group_scope = azurerm_resource_group.gludd_rg.id
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
  scope       = local.resource_group_scope
  description = "Deploy, inspect, and remove Gludd-owned Container Apps in one pre-created GPU environment."

  permissions {
    actions = [
      "Microsoft.App/managedEnvironments/read",
      "Microsoft.App/managedEnvironments/join/action",
      "Microsoft.App/managedEnvironments/usages/read",
      "Microsoft.App/managedEnvironments/workloadProfileStates/read",
      "Microsoft.App/containerApps/read",
      "Microsoft.App/containerApps/write",
      "Microsoft.App/containerApps/delete",
      "Microsoft.App/containerApps/revisions/read",
      "Microsoft.App/locations/containerAppOperationResults/read",
      "Microsoft.App/locations/containerAppOperationStatuses/read",
    ]
    not_actions = []
  }

  assignable_scopes = [local.resource_group_scope]
}

# By default the role belongs to the managed identity above.  Supplying an
# app/service-principal object id makes the exact same role usable by gludd
# running outside Azure with AZURE_* service-principal credentials.
resource "azurerm_role_assignment" "accelerator_deployer" {
  scope                            = local.resource_group_scope
  role_definition_id              = azurerm_role_definition.accelerator_deployer.role_definition_resource_id
  principal_id                     = local.operator_principal_id
  skip_service_principal_aad_check = true
}
