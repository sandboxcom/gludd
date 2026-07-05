variable "image" {
  description = "Container image for the inference engine. Defaults to the engine's canonical image."
  type        = string
  default     = "vllm/vllm-openai:latest"
}

variable "gpus" {
  description = "Number of GPUs to expose to the inference container."
  type        = number
  default     = 1
}

variable "model" {
  description = "Model identifier served by the inference engine. Must match the ComputeConfig.model_name allowlist."
  type        = string
}

variable "region" {
  description = "GCP region the stack deploys into."
  type        = string
  default     = "us-central1"
}

variable "instance_type" {
  description = "GCP machine type. Resolved by the stack from GPU type; passed through here for tagging/labeling."
  type        = string
  default     = "g2-standard-4"
}

variable "extra_args" {
  description = "Extra arguments appended to the inference server invocation."
  type        = string
  default     = ""
}

variable "max_cost_usd" {
  description = "Cost watchdog ceiling (USD). Mirrors ComputeConfig.max_cost_usd."
  type        = number
  default     = 10
}

variable "timeout_minutes" {
  description = "Cost watchdog TTL (minutes). Mirrors ComputeConfig.timeout_minutes."
  type        = number
  default     = 60
}

variable "poll_interval_seconds" {
  description = "Seconds between watchdog cost/TTL polls."
  type        = number
  default     = 60
}

variable "allowed_cidr" {
  description = "CIDR permitted to reach the vLLM inference port (8000). Defaults to 0.0.0.0/0; narrow via tfvars for production."
  type        = string
  default     = "0.0.0.0/0"
}

variable "ssh_cidr" {
  description = "CIDR permitted to reach port 22 (SSH). Should be the operator's egress CIDR."
  type        = string
  default     = "0.0.0.0/0"
}

variable "use_spot" {
  description = "When true, launches the compute instance as a preemptible VM for cost savings. When false, uses standard on-demand pricing."
  type        = bool
  default     = true
}

variable "boot_image" {
  description = "Boot disk image for the GCE instance. Should be an Ubuntu 22.04+ or similar image (e.g. projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts)."
  type        = string
  default     = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
}
