# Input variables for the vllm-server module.
# Extracted from ComputeConfig fields consumed by
# src/general_ludd/infra/terraform.py::_generate_* for engine == VLLM.

variable "image" {
  description = "Container image for vllm serve. Defaults to the upstream vllm image."
  type        = string
  default     = "vllm/vllm-openai:latest"
}

variable "gpus" {
  description = "Number of GPUs to expose to the container (--gpus)."
  type        = number
  default     = 1
}

variable "model" {
  description = "Model identifier passed to `vllm serve --model`. Must match the ComputeConfig.model_name allowlist."
  type        = string
}

variable "extra_args" {
  description = "Extra arguments appended to the `vllm serve` invocation."
  type        = string
  default     = ""
}

variable "region" {
  description = "Provider region/zone the stack deploys into. The module itself is region-agnostic; the stack's provider block consumes this."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Provider-specific instance/machine type. Resolved by the stack from GPU type; passed through here for tagging/labeling."
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

variable "guided_decoding_backend" {
  description = "Guided decoding backend for vLLM structured output generation. One of: outlines, xgrammar, lm-format-enforcer, or empty string to disable."
  type        = string
  default     = "outlines"

  validation {
    condition     = contains(["outlines", "xgrammar", "lm-format-enforcer", ""], var.guided_decoding_backend)
    error_message = "guided_decoding_backend must be one of: outlines, xgrammar, lm-format-enforcer, or empty string."
  }
}

variable "enable_structured_outputs" {
  description = "Whether to enable server-side structured/guided decoding via --guided-decoding-backend."
  type        = bool
  default     = true
}
