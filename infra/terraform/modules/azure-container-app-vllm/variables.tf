variable "deployment_name" {
  description = "Unique Gludd deployment identifier used only for the Container App."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,39}$", var.deployment_name))
    error_message = "deployment_name must contain 3-40 lowercase letters, digits, or hyphens."
  }
}

variable "resource_group_id" {
  description = "Exact existing resource-group ID; this module never creates or deletes it."
  type        = string

  validation {
    condition = can(regex(
      "^/subscriptions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/resourceGroups/[A-Za-z0-9._()-]{1,90}$",
      var.resource_group_id,
    ))
    error_message = "resource_group_id must be one canonical existing Azure resource-group ID."
  }
}

variable "managed_environment_id" {
  description = "Exact existing Container Apps managed-environment ID verified by preflight."
  type        = string

  validation {
    condition = can(regex(
      "^/subscriptions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/resourceGroups/[A-Za-z0-9._()-]{1,90}/providers/Microsoft.App/managedEnvironments/[A-Za-z0-9][A-Za-z0-9-]{1,59}$",
      var.managed_environment_id,
    ))
    error_message = "managed_environment_id must be one canonical existing environment ID."
  }
}

variable "workload_profile_name" {
  description = "Existing environment workload-profile name verified by preflight."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]{1,59}$", var.workload_profile_name))
    error_message = "workload_profile_name must be one bounded Azure name."
  }
}

variable "workload_profile_type" {
  description = "Exact profile type selected by Gludd's right-sizing proof."
  type        = string
}

variable "region" {
  description = "Azure region of the existing managed environment."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{2,32}$", var.region))
    error_message = "region must be one canonical Azure region."
  }
}

variable "container_image" {
  description = "Digest-pinned linux/amd64 OpenAI-compatible vLLM image."
  type        = string

  validation {
    condition = can(regex(
      "^[A-Za-z0-9][A-Za-z0-9._/-]*@sha256:[0-9a-f]{64}$",
      var.container_image,
    ))
    error_message = "container_image must be digest-pinned with @sha256:<64 lowercase hex>."
  }
}

variable "model_name" {
  description = "Public Hugging Face model identifier served by vLLM."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.model_name))
    error_message = "model_name must be one canonical owner/repository pair."
  }
}

variable "model_revision" {
  description = "Immutable Hugging Face model commit."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.model_revision))
    error_message = "model_revision must be a 40-character commit SHA."
  }
}

variable "gpu_type" {
  description = "Gludd GPU identifier mapped to an Azure serverless GPU profile."
  type        = string

  validation {
    condition     = contains(["t4", "a100_80"], var.gpu_type)
    error_message = "gpu_type must be t4 or a100_80 for Azure Container Apps."
  }
}

variable "gpu_count" {
  description = "Azure serverless GPU profiles expose exactly one GPU."
  type        = number

  validation {
    condition     = var.gpu_count == 1
    error_message = "gpu_count must equal 1 for Azure Container Apps serverless GPU."
  }
}

variable "allowed_cidr" {
  description = "One IPv4 /32 allowed to reach the otherwise unauthenticated endpoint."
  type        = string

  validation {
    condition = (
      can(cidrhost(var.allowed_cidr, 0)) &&
      endswith(var.allowed_cidr, "/32") &&
      length(regexall(":", var.allowed_cidr)) == 0
    )
    error_message = "allowed_cidr must be one IPv4 /32."
  }
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

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at_utc))
    error_message = "expires_at_utc must be a whole-second UTC RFC3339 timestamp."
  }
}

variable "owner_token" {
  description = "Bounded run owner used to prove cleanup ownership."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{7,63}$", var.owner_token))
    error_message = "owner_token must be one 8-64 character lowercase run label."
  }
}

variable "trace_id" {
  description = "Secret-free correlation ID emitted by every live-proof phase."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.trace_id))
    error_message = "trace_id must be 32 lowercase hexadecimal characters."
  }
}

variable "min_replicas" {
  description = "Minimum paid GPU replicas; Gludd requires scale-to-zero when idle."
  type        = number
  default     = 0

  validation {
    condition     = var.min_replicas == 0
    error_message = "min_replicas must remain zero for paid-idle safety."
  }
}

variable "max_replicas" {
  description = "Maximum GPU replicas derived from bounded simultaneous task demand."
  type        = number
  default     = 1

  validation {
    condition     = var.max_replicas >= 1 && var.max_replicas <= 100 && floor(var.max_replicas) == var.max_replicas
    error_message = "max_replicas must be an integer in 1..100."
  }
}

variable "http_concurrent_requests" {
  description = "Per-replica HTTP concurrency derived by the topology planner."
  type        = number
  default     = 1

  validation {
    condition     = var.http_concurrent_requests >= 1 && var.http_concurrent_requests <= 100000 && floor(var.http_concurrent_requests) == var.http_concurrent_requests
    error_message = "http_concurrent_requests must be an integer in 1..100000."
  }
}

variable "vllm_context_length" {
  description = "Maximum token context selected by the immutable model-fit plan."
  type        = number
  default     = 4096

  validation {
    condition     = var.vllm_context_length >= 512 && var.vllm_context_length <= 32768
    error_message = "vllm_context_length must be in 512..32768."
  }
}

variable "vllm_max_num_seqs" {
  description = "Maximum concurrent vLLM sequences selected for the GPU profile."
  type        = number
  default     = 8

  validation {
    condition     = var.vllm_max_num_seqs >= 1 && var.vllm_max_num_seqs <= 64
    error_message = "vllm_max_num_seqs must be in 1..64."
  }
}

variable "vllm_gpu_memory_utilization" {
  description = "Fraction of GPU memory vLLM may reserve for weights and KV cache."
  type        = number
  default     = 0.9

  validation {
    condition     = var.vllm_gpu_memory_utilization >= 0.5 && var.vllm_gpu_memory_utilization <= 0.95
    error_message = "vllm_gpu_memory_utilization must be in 0.5..0.95."
  }
}

variable "vllm_enforce_eager" {
  description = "Whether vLLM must disable CUDA graph execution for this candidate."
  type        = bool
  default     = false
}

variable "vllm_enable_prefix_caching" {
  description = "Whether vLLM may reuse matching prompt-prefix cache blocks."
  type        = bool
  default     = true
}

variable "vllm_enable_chunked_prefill" {
  description = "Whether vLLM may split long prompt prefills into bounded chunks."
  type        = bool
  default     = true
}

variable "vllm_kv_cache_dtype" {
  description = "KV-cache data type selected by the immutable model-fit plan."
  type        = string
  default     = "auto"
}

variable "vllm_quantization" {
  description = "Optional vLLM quantization backend selected for the pinned model."
  type        = string
  default     = ""
}
