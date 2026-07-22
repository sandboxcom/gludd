# Input variables for the gpu-cost-watchdog module.

variable "max_cost_usd" {
  description = "Cost ceiling (USD). The watchdog self-terminates the instance once cumulative spend is estimated to reach this. 0 disables the cost bound."
  type        = number
  default     = 10
}

variable "timeout_minutes" {
  description = "Wall-clock TTL (minutes). The watchdog self-terminates after this many minutes of uptime, regardless of cost. 0 disables the TTL bound."
  type        = number
  default     = 60
}

variable "poll_interval_seconds" {
  description = "Seconds between watchdog polls. Each tick re-queries the cloud price API (when supported) and re-evaluates the cost/TTL bounds."
  type        = number
  default     = 60
}

variable "region" {
  description = "Cloud region/zone the instance runs in. Passed to the cloud terminate/price APIs. May be empty when the watchdog resolves region from instance metadata."
  type        = string
  default     = ""
}

variable "instance_id" {
  description = "Provider-assigned instance id of the GPU VM being watched. May be empty; the script falls back to the metadata service."
  type        = string
  default     = ""
}

variable "cloud" {
  description = "Cloud provider dispatch key. Selects the price-query + terminate code paths. One of: aws, gcp, azure, vsphere, runpod, vast, kubernetes, qemu."
  type        = string
  default     = "aws"

  validation {
    condition     = contains(["aws", "gcp", "azure", "vsphere", "runpod", "vast", "kubernetes", "qemu"], var.cloud)
    error_message = "cloud must be one of: aws, gcp, azure, vsphere, runpod, vast, kubernetes, qemu."
  }
}
