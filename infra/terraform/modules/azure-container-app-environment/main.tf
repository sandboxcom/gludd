terraform {
  required_providers {
    azapi = {
      source = "Azure/azapi"
    }
  }
}

resource "azapi_resource" "managed_environment" {
  type      = "Microsoft.App/managedEnvironments@2025-07-01"
  name      = var.environment_name
  parent_id = var.resource_group_id
  location  = var.region

  tags = {
    "gludd-managed-by"       = "general-ludd"
    "gludd-lifecycle-version" = "1"
    "gludd-owner-digest"      = var.owner_digest
    "gludd-plan-digest"       = var.plan_digest
    "gludd-expires-at"        = var.expires_at_utc
  }

  body = {
    properties = {
      workloadProfiles = [
        for profile in var.workload_profiles : {
          name                = profile.profile_name
          workloadProfileType = profile.workload_profile_type
        }
      ]
    }
  }

  schema_validation_enabled = true
  response_export_values = [
    "id",
    "name",
    "properties.provisioningState",
    "properties.workloadProfiles",
    "tags",
  ]
}
