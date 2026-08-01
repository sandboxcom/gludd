variable "deployment_name" {
  description = "Unique Gludd deployment identifier used to namespace all Azure resources."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{3,40}$", var.deployment_name))
    error_message = "deployment_name must contain 3-40 letters, digits, underscores, or hyphens."
  }
}

variable "region" {
  description = "Azure region with the requested serverless GPU workload profile."
  type        = string
}

variable "container_image" {
  description = "CUDA-capable OpenAI-compatible vLLM image."
  type        = string
}

variable "model_name" {
  description = "Hugging Face model identifier served by vLLM."
  type        = string
}

variable "gpu_type" {
  description = "Gludd GPU identifier mapped to an Azure serverless GPU workload profile."
  type        = string

  validation {
    condition     = contains(["t4", "a100_40", "a100_80"], var.gpu_type)
    error_message = "gpu_type must be t4, a100_40, or a100_80 for Azure Container Apps."
  }
}

variable "gpu_count" {
  description = "GPU count per replica; Azure serverless GPU profiles expose exactly one GPU."
  type        = number

  validation {
    condition     = var.gpu_count == 1
    error_message = "gpu_count must equal 1 for Azure Container Apps serverless GPU."
  }
}

variable "allowed_cidr" {
  description = "Comma-separated CIDRs allowed to reach the unauthenticated inference endpoint."
  type        = string
  default     = "127.0.0.1/32"
}

variable "max_cost_usd" {
  description = "Deployment cost ceiling recorded on resources and enforced by Gludd."
  type        = number
}

variable "timeout_minutes" {
  description = "Deployment TTL recorded on resources and enforced by Gludd."
  type        = number
}

variable "vllm_context_length" {
  type    = number
  default = 4096
}

variable "vllm_max_num_seqs" {
  type    = number
  default = 8
}

variable "vllm_gpu_memory_utilization" {
  type    = number
  default = 0.9
}

variable "vllm_enforce_eager" {
  type    = bool
  default = false
}

variable "vllm_enable_prefix_caching" {
  type    = bool
  default = true
}

variable "vllm_enable_chunked_prefill" {
  type    = bool
  default = true
}

variable "vllm_kv_cache_dtype" {
  type    = string
  default = "auto"
}

variable "vllm_quantization" {
  type    = string
  default = ""
}
