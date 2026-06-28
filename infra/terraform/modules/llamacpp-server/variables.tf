# Input variables for the llamacpp-server module.
# Mirrors vllm-server/variables.tf; default image is the llama.cpp server image
# (matches _default_image in terraform.py for InferenceEngine.LLAMACPP).

variable "image" {
  description = "Container image for the llama.cpp server. Defaults to the upstream image."
  type        = string
  default     = "ghcr.io/ggerganov/llama.cpp:server"
}

variable "gpus" {
  description = "Number of GPUs to expose to the container (--gpus)."
  type        = number
  default     = 1
}

variable "model" {
  description = "Model path/identifier passed to `llama_cpp.server -m`. Must match the ComputeConfig.model_name allowlist."
  type        = string
}

variable "extra_args" {
  description = "Extra arguments appended to the llama.cpp server invocation (e.g. --n-gpu-layers, --ctx-size)."
  type        = string
  default     = ""
}

variable "region" {
  description = "Provider region/zone the stack deploys into. Region-agnostic at module scope."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Provider-specific instance/machine type, resolved by the stack from GPU type."
  type        = string
  default     = "g5.xlarge"
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
