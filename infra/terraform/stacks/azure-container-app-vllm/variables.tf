variable "deployment_name" {
  description = "Unique Gludd deployment identifier used only for the Container App."
  type        = string
}

variable "resource_group_id" {
  description = "Exact existing resource-group ID; this stack never creates or deletes it."
  type        = string
}

variable "managed_environment_id" {
  description = "Exact existing Container Apps managed-environment ID verified by preflight."
  type        = string
}

variable "workload_profile_name" {
  description = "Existing environment workload-profile name verified by preflight."
  type        = string
}

variable "workload_profile_type" {
  description = "Exact profile type selected by Gludd's right-sizing proof."
  type        = string
}

variable "region" {
  description = "Azure region of the existing managed environment."
  type        = string
}

variable "container_image" {
  description = "Digest-pinned linux/amd64 OpenAI-compatible vLLM image."
  type        = string
}

variable "model_name" {
  description = "Public Hugging Face model identifier served by vLLM."
  type        = string
}

variable "model_revision" {
  description = "Immutable Hugging Face model commit."
  type        = string
}

variable "gpu_type" {
  description = "Gludd GPU identifier mapped to an Azure serverless GPU profile."
  type        = string
}

variable "gpu_count" {
  description = "Azure serverless GPU profiles expose exactly one GPU."
  type        = number
}

variable "allowed_cidr" {
  description = "One IPv4 /32 allowed to reach the otherwise unauthenticated endpoint."
  type        = string
}

variable "max_cost_usd" {
  description = "Hard live-proof cost reservation enforced by the external cleanup supervisor."
  type        = number

  validation {
    condition     = var.max_cost_usd > 0 && var.max_cost_usd <= 5
    error_message = "max_cost_usd must be greater than zero and no more than 5."
  }
}

variable "timeout_minutes" {
  description = "Hard app lifetime enforced by the external cleanup supervisor."
  type        = number

  validation {
    condition     = var.timeout_minutes > 0 && var.timeout_minutes <= 60
    error_message = "timeout_minutes must be greater than zero and no more than 60."
  }
}

variable "expires_at_utc" {
  description = "Immutable RFC3339 cleanup deadline calculated before apply."
  type        = string
}

variable "owner_token" {
  description = "Bounded run owner used to prove cleanup ownership."
  type        = string
}

variable "trace_id" {
  description = "Secret-free correlation ID emitted by every live-proof phase."
  type        = string
}

variable "use_spot" {
  description = "Spot capacity is unsupported for Azure Container Apps serverless GPU."
  type        = bool
  default     = false

  validation {
    condition     = !var.use_spot
    error_message = "use_spot must remain false for Azure Container Apps GPU."
  }
}

variable "vllm_context_length" {
  description = "Maximum token context selected by the immutable model-fit plan."
  type        = number
}

variable "vllm_max_num_seqs" {
  description = "Maximum concurrent vLLM sequences selected for the GPU profile."
  type        = number
}

variable "vllm_gpu_memory_utilization" {
  description = "Fraction of GPU memory vLLM may reserve for weights and KV cache."
  type        = number
}

variable "vllm_enforce_eager" {
  description = "Whether vLLM must disable CUDA graph execution for this candidate."
  type        = bool
}

variable "vllm_enable_prefix_caching" {
  description = "Whether vLLM may reuse matching prompt-prefix cache blocks."
  type        = bool
}

variable "vllm_enable_chunked_prefill" {
  description = "Whether vLLM may split long prompt prefills into bounded chunks."
  type        = bool
}

variable "vllm_kv_cache_dtype" {
  description = "KV-cache data type selected by the immutable model-fit plan."
  type        = string
}

variable "vllm_quantization" {
  description = "Optional vLLM quantization backend selected for the pinned model."
  type        = string
}
