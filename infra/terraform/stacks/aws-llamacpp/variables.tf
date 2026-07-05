variable "image" {
  description = "Container image for the inference engine. Defaults to the engine's canonical image."
  type        = string
  default     = "ghcr.io/ggerganov/llama.cpp:server"
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
  description = "AWS region the stack deploys into."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "AWS instance type. Resolved by the stack from GPU type; passed through here for tagging/labeling."
  type        = string
  default     = "g5.xlarge"
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

variable "use_spot" {
  description = "When true, launches the EC2 instance as a spot instance for cost savings. When false, uses on-demand pricing."
  type        = bool
  default     = true
}

variable "spot_price" {
  description = "Maximum spot price per instance-hour. Empty string uses current market price. Only used when use_spot is true."
  type        = string
  default     = ""
}

variable "ami_id" {
  description = "AMI ID for the EC2 instance. Should be an Ubuntu 22.04+ or similar cloud-init-ready image."
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID to launch the EC2 instance into."
  type        = string
}

variable "vpc_security_group_ids" {
  description = "List of security group IDs to attach to the instance."
  type        = list(string)
  default     = []
}
