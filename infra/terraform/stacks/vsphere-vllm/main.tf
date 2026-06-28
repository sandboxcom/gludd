terraform {
  required_providers {
    vsphere = {
      source  = "hashicorp/vsphere"
      version = "~> 2.8"
    }
  }
}

variable "vsphere_user" {
  description = "vSphere user (sourced from OpenBao / SecretsManager at deploy time)."
  type        = string
  sensitive   = true
}

variable "vsphere_password" {
  description = "vSphere password (sourced from OpenBao / SecretsManager at deploy time)."
  type        = string
  sensitive   = true
}

variable "vsphere_server" {
  description = "vCenter / ESXi server FQDN or IP (sourced from OpenBao / SecretsManager at deploy time)."
  type        = string
}

variable "datacenter" {
  description = "vSphere datacenter name."
  type        = string
}

variable "cluster" {
  description = "vSphere cluster name."
  type        = string
}

variable "datastore" {
  description = "vSphere datastore name for the VM disk."
  type        = string
}

variable "network" {
  description = "vSphere port-group / network label for the VM NIC."
  type        = string
}

variable "gpu_type" {
  description = "GPU type identifier (matches general_ludd.infra.compute.GPUType values)."
  type        = string
  default     = "a100_80"
}

variable "gpu_count" {
  description = "Number of GPUs to attach."
  type        = number
  default     = 1
}

variable "disk_size_gb" {
  description = "Boot disk size in GB."
  type        = number
  default     = 100
}

variable "engine" {
  description = "Inference engine (vllm | llamacpp)."
  type        = string
  default     = "vllm"
}

variable "model_name" {
  description = "Model identifier served by the inference engine."
  type        = string
}

variable "container_image" {
  description = "Container image to run; defaults to engine's canonical image."
  type        = string
  default     = ""
}

variable "max_cost_usd" {
  description = "Cost watchdog ceiling."
  type        = string
  default     = "10.0"
}

variable "timeout_minutes" {
  description = "TTL watchdog ceiling."
  type        = string
  default     = "60.0"
}

variable "user_data_script" {
  description = "cloud-init user-data handed off to the vllm-server module."
  type        = string
  default     = ""
}

variable "allowed_cidr" {
  description = "CIDR permitted to reach the inference endpoint (:8000). Defaults to loopback-only."
  type        = string
  default     = "127.0.0.1/32"
}

provider "vsphere" {
  user                 = var.vsphere_user
  password             = var.vsphere_password
  vsphere_server       = var.vsphere_server
  allow_unverified_ssl = true
}

module "vllm_server" {
  source = "../modules/vllm-server"

  datacenter       = var.datacenter
  cluster          = var.cluster
  datastore        = var.datastore
  network          = var.network
  gpu_type         = var.gpu_type
  gpu_count        = var.gpu_count
  disk_size_gb     = var.disk_size_gb
  engine           = var.engine
  model_name       = var.model_name
  container_image  = var.container_image
  max_cost_usd     = var.max_cost_usd
  timeout_minutes  = var.timeout_minutes
  user_data_script = var.user_data_script
  allowed_cidr     = var.allowed_cidr
}

output "instance_ip" {
  description = "Primary IP of the vLLM inference VM."
  value       = module.vllm_server.instance_ip
}

output "endpoint_url" {
  description = "OpenAI-compatible v1 endpoint."
  value       = module.vllm_server.endpoint_url
}
