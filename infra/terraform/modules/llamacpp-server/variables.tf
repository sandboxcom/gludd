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

variable "grammar_file" {
  description = "Path to a GBNF grammar file for constrained generation. Passed to llama.cpp server as --grammar. Leave empty to disable."
  type        = string
  default     = ""
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
  description = "Maximum context length (n_ctx) for the model."
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
    condition     = contains(["", "fp16", "bf16", "fp8", "int8", "q8_0", "q6_k", "q5_k_m", "q4_k_m"], var.quantization)
    error_message = "quantization must be one of: '', fp16, bf16, fp8, int8, q8_0, q6_k, q5_k_m, q4_k_m."
  }
}

variable "threads" {
  description = "Number of CPU threads (n_threads) for the inference server (0 = auto)."
  type        = number
  default     = 0
}

variable "max_num_seqs" {
  description = "Maximum number of concurrent sequences."
  type        = number
  default     = 4
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
  description = "Enable chunked prefill."
  type        = bool
  default     = true
}

variable "kv_cache_dtype" {
  description = "KV cache data type (cache_type_k / cache_type_v for llama.cpp)."
  type        = string
  default     = "auto"

  validation {
    condition     = contains(["auto", "f16", "q8_0", "q4_0"], var.kv_cache_dtype)
    error_message = "kv_cache_dtype must be one of: auto, f16, q8_0, q4_0."
  }
}
