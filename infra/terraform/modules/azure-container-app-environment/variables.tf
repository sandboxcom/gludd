variable "environment_name" {
  description = "Deterministic Gludd-owned Container Apps environment name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$", var.environment_name))
    error_message = "environment_name must be a bounded lowercase Azure resource name."
  }
}

variable "resource_group_id" {
  description = "Exact existing resource-group boundary; this module never creates or deletes it."
  type        = string

  validation {
    condition     = can(regex("^/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9_.()\\-]+$", var.resource_group_id))
    error_message = "resource_group_id must identify one existing Azure resource group."
  }
}

variable "region" {
  description = "Canonical Azure region selected after serverless-GPU availability discovery."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,31}$", var.region))
    error_message = "region must be a canonical Azure location identifier."
  }
}

variable "workload_profiles" {
  description = "One or two right-sized serverless-GPU profiles required by the runner topology."
  type        = list(object({ profile_name = string, workload_profile_type = string }))

  validation {
    condition     = length(var.workload_profiles) >= 1 && length(var.workload_profiles) <= 2
    error_message = "workload_profiles must contain one or two serverless-GPU profiles."
  }

  validation {
    condition     = length(distinct([for profile in var.workload_profiles : "${profile.profile_name}:${profile.workload_profile_type}"])) == length(var.workload_profiles)
    error_message = "workload_profiles must not contain duplicate profiles."
  }

  validation {
    condition     = alltrue([for profile in var.workload_profiles : (profile.profile_name == "gpu-t4" && profile.workload_profile_type == "Consumption-GPU-NC8as-T4") || (profile.profile_name == "gpu-a100" && profile.workload_profile_type == "Consumption-GPU-NC24-A100")])
    error_message = "workload_profiles must use reviewed serverless-GPU name/type pairs."
  }
}

variable "owner_digest" {
  description = "Stable SHA-256 workspace owner proof required before reconciliation or teardown."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.owner_digest))
    error_message = "owner_digest must be a lowercase SHA-256 digest."
  }
}

variable "plan_digest" {
  description = "SHA-256 digest binding the environment to its audited runner topology."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.plan_digest))
    error_message = "plan_digest must be a lowercase SHA-256 digest."
  }
}

variable "expires_at_utc" {
  description = "RFC3339 UTC deadline after which the lifecycle supervisor must reconcile cleanup."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at_utc))
    error_message = "expires_at_utc must be an RFC3339 UTC timestamp at second precision."
  }
}
