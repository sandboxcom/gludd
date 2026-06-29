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
