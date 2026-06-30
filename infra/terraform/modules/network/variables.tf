# Input variables for the network module.

variable "cloud" {
  description = "Cloud provider dispatch key. One of: aws, gcp, azure, vsphere, runpod, vast. runpod and vast are N/A (managed internally)."
  type        = string

  validation {
    condition     = contains(["aws", "gcp", "azure", "vsphere", "runpod", "vast"], var.cloud)
    error_message = "cloud must be one of: aws, gcp, azure, vsphere, runpod, vast."
  }
}

variable "vllm_port" {
  description = "Port the vLLM inference server listens on. Opened to var.vllm_allowed_cidr."
  type        = number
  default     = 8000
}

variable "vllm_allowed_cidr" {
  description = "CIDR permitted to reach var.vllm_port. Defaults to 0.0.0.0/0 (operator-configurable; narrow via tfvars for production)."
  type        = string
  default     = "0.0.0.0/0"
}

variable "ssh_cidr" {
  description = "CIDR permitted to reach port 22 (SSH). Should be the operator's egress CIDR — never 0.0.0.0/0 in production."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to all resource names (security group, firewall rules, NSG). Defaults to \"vllm\"."
  type        = string
  default     = ""
}

variable "region" {
  description = "Cloud region/zone. Required by Azure (NSG needs a location). Other providers derive region from the provider block."
  type        = string
  default     = ""
}

variable "azure_resource_group" {
  description = "Azure resource group name. Required only when provider == \"azure\" (NSG + rules are rg-scoped)."
  type        = string
  default     = ""
}

variable "vsphere_network" {
  description = "vSphere dvSwitch/distributed-port-group name. When non-empty and provider == \"vsphere\", a null_resource runs govc to record/verify the port-group binding (vSphere firewalling itself lives at the dvSwitch layer)."
  type        = string
  default     = ""
}
