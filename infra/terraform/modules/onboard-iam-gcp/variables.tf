# Input variables for the onboard-iam-gcp module.

variable "project_id" {
  description = "GCP project id where gludd will provision GPU instances."
  type        = string
}

variable "service_account_name" {
  description = "Service-account id (account_id) for the gludd operator."
  type        = string
  default     = "gludd-compute-operator"
}

variable "display_name" {
  description = "Human-readable display name for the service account."
  type        = string
  default     = "Gludd compute operator (ephemeral GPU provisioning)"
}

variable "create_key" {
  description = "Whether to create a service-account JSON key for local/CI auth. Disabled by default — prefer Application Default Credentials."
  type        = bool
  default     = false
}
