variable "deployment_name" {
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
}

variable "timeout_minutes" {
  type = number
}

variable "use_spot" {
  description = "Spot capacity is not supported for Azure Container Apps; retained as a false-only interface default."
  type        = bool
  default     = false

  validation {
    condition     = !var.use_spot
    error_message = "use_spot must remain false because Azure Container Apps GPU profiles do not support spot capacity."
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
