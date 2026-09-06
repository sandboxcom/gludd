variable "deployment_name" {
  type = string
}

variable "resource_group_id" {
  type = string
}

variable "managed_environment_id" {
  type = string
}

variable "workload_profile_name" {
  type = string
}

variable "workload_profile_type" {
  type = string
}

variable "region" {
  type = string
}

variable "container_image" {
  type = string
}

variable "model_name" {
  type = string
}

variable "model_revision" {
  type = string
}

variable "gpu_type" {
  type = string
}

variable "gpu_count" {
  type = number
}

variable "allowed_cidr" {
  type = string
}

variable "max_cost_usd" {
  type = number

  validation {
    condition     = var.max_cost_usd > 0 && var.max_cost_usd <= 5
    error_message = "max_cost_usd must be greater than zero and no more than 5."
  }
}

variable "timeout_minutes" {
  type = number

  validation {
    condition     = var.timeout_minutes > 0 && var.timeout_minutes <= 60
    error_message = "timeout_minutes must be greater than zero and no more than 60."
  }
}

variable "expires_at_utc" {
  type = string
}

variable "owner_token" {
  type = string
}

variable "trace_id" {
  type = string
}

variable "use_spot" {
  type    = bool
  default = false

  validation {
    condition     = !var.use_spot
    error_message = "use_spot must remain false for Azure Container Apps GPU."
  }
}

variable "vllm_context_length" {
  type = number
}

variable "vllm_max_num_seqs" {
  type = number
}

variable "vllm_gpu_memory_utilization" {
  type = number
}

variable "vllm_enforce_eager" {
  type = bool
}

variable "vllm_enable_prefix_caching" {
  type = bool
}

variable "vllm_enable_chunked_prefill" {
  type = bool
}

variable "vllm_kv_cache_dtype" {
  type = string
}

variable "vllm_quantization" {
  type = string
}
