variable "libvirt_uri" {
  description = "Libvirt connection URI. Defaults to local system daemon."
  type        = string
  default     = "qemu:///system"
}

variable "name" {
  description = "QEMU VM display name."
  type        = string
  default     = "gludd-vllm"
}

variable "vcpus" {
  description = "Number of vCPUs for the VM."
  type        = number
  default     = 8
}

variable "memory_mb" {
  description = "RAM in megabytes for the VM."
  type        = number
  default     = 65536
}

variable "disk_size_gb" {
  description = "Root disk size in gigabytes."
  type        = number
  default     = 200
}

variable "base_image_url" {
  description = "URL of the base OS qcow2 cloud image."
  type        = string
  default     = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

variable "storage_pool" {
  description = "Libvirt storage pool name."
  type        = string
  default     = "default"
}

variable "network_name" {
  description = "Libvirt network name."
  type        = string
  default     = "default"
}

variable "host_port" {
  description = "Host port forwarded to VM inference port 8000."
  type        = number
  default     = 8000
}

variable "ssh_public_key" {
  description = "SSH public key injected into the VM via cloud-init."
  type        = string
}

variable "image" {
  description = "Container image for vLLM. Defaults to the upstream vllm image."
  type        = string
  default     = "vllm/vllm-openai:latest"
}

variable "gpus" {
  description = "Number of GPUs to expose to the inference container."
  type        = number
  default     = 1
}

variable "model" {
  description = "Model identifier served by vLLM. Must match the ComputeConfig.model_name allowlist."
  type        = string
}

variable "region" {
  description = "QEMU host region label. Informational only; the VM runs on the local host."
  type        = string
  default     = "local"
}

variable "instance_type" {
  description = "Descriptive instance profile for tagging/labeling."
  type        = string
  default     = "qemu-gpu"
}

variable "extra_args" {
  description = "Extra arguments appended to the vLLM serve invocation."
  type        = string
  default     = ""
}

variable "max_cost_usd" {
  description = "Cost watchdog ceiling (USD). Informational for on-prem QEMU; no cloud billing applies."
  type        = number
  default     = 0
}

variable "timeout_minutes" {
  description = "Cost watchdog TTL (minutes)."
  type        = number
  default     = 0
}
