terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
  }
}

provider "azapi" {
  skip_provider_registration = true
}

module "environment" {
  source = "../../modules/azure-container-app-environment"

  environment_name = var.environment_name
  resource_group_id = var.resource_group_id
  region = var.region
  workload_profiles = var.workload_profiles
  owner_digest = var.owner_digest
  plan_digest = var.plan_digest
  expires_at_utc = var.expires_at_utc
}
