# Input variables for the onboard-iam-azure module.

variable "subscription_id" {
  description = "Azure subscription id where gludd will provision GPU VMs."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group for the gludd operator identity (and later its VMs)."
  type        = string
  default     = "gludd-rg"
}

variable "location" {
  description = "Azure region for the resource group + identity."
  type        = string
  default     = "eastus"
}

variable "identity_name" {
  description = "Name of the gludd operator user-assigned managed identity."
  type        = string
  default     = "gludd-compute-operator"
}

variable "operator_principal_id" {
  description = "Optional object id of an existing app/service principal. Defaults to the managed identity created by this module."
  type        = string
  default     = null
  nullable    = true
}
