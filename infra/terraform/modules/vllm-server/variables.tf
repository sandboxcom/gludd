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

variable "workload_type" {
  description = "Workload pattern to optimize for: batch_inference, realtime_api, fine_tuning, speculative_decoding, embedding_generation."
  type        = string
  default     = "batch_inference"

  validation {
    condition     = contains(["batch_inference", "realtime_api", "fine_tuning", "speculative_decoding", "embedding_generation"], var.workload_type)
    error_message = "workload_type must be one of: batch_inference, realtime_api, fine_tuning, speculative_decoding, embedding_generation."
  }
}

variable "context_length" {
  description = "Maximum context length (max_model_len) for the model."
  type        = number
  default     = 32768
}

variable "max_tokens" {
  description = "Maximum tokens to generate per request."
  type        = number
  default     = 4096
}

variable "batch_size" {
  description = "Maximum batch size for the serving engine."
  type        = number
  default     = 256
}

variable "tensor_parallel" {
  description = "Tensor parallelism degree (0 = auto, auto-determined by optimizer)."
  type        = number
  default     = 0
}

variable "gpu_memory_utilization" {
  description = "GPU memory utilization fraction (0.0-0.95)."
  type        = number
  default     = 0.90

  validation {
    condition     = var.gpu_memory_utilization > 0 && var.gpu_memory_utilization <= 0.95
    error_message = "gpu_memory_utilization must be in (0, 0.95]."
  }
}

variable "quantization" {
  description = "Weight quantization method. Empty string = auto (determined by optimizer by VRAM)."
  type        = string
  default     = ""

  validation {
    condition     = contains(["", "fp16", "bf16", "fp8", "int8", "awq", "gptq"], var.quantization)
    error_message = "quantization must be one of: '', fp16, bf16, fp8, int8, awq, gptq."
  }
}

variable "threads" {
  description = "Number of CPU threads for the inference server (0 = auto)."
  type        = number
  default     = 0
}

variable "max_num_seqs" {
  description = "Maximum number of concurrent sequences."
  type        = number
  default     = 256
}

variable "enforce_eager" {
  description = "Disable CUDA graphs (enforce eager mode)."
  type        = bool
  default     = false
}

variable "enable_prefix_caching" {
  description = "Enable automatic prefix caching (APC)."
  type        = bool
  default     = true
}

variable "enable_chunked_prefill" {
  description = "Enable chunked prefill for better throughput under load."
  type        = bool
  default     = true
}

variable "kv_cache_dtype" {
  description = "KV cache data type (auto = match weight dtype)."
  type        = string
  default     = "auto"

  validation {
    condition     = contains(["auto", "fp8", "fp16"], var.kv_cache_dtype)
    error_message = "kv_cache_dtype must be one of: auto, fp8, fp16."
  }
}
