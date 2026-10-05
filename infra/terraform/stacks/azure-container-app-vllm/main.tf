terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
  }
}

provider "azapi" {
  skip_provider_registration = true
}

module "gpu_cost_watchdog" {
  source = "../../modules/gpu-cost-watchdog"

  max_cost_usd    = var.max_cost_usd
  timeout_minutes = var.timeout_minutes
  region          = var.region
  cloud           = "azure"
}

module "vllm_server" {
  source = "../../modules/azure-container-app-vllm"

  deployment_name                = var.deployment_name
  resource_group_id              = var.resource_group_id
  managed_environment_id         = var.managed_environment_id
  workload_profile_name          = var.workload_profile_name
  workload_profile_type          = var.workload_profile_type
  region                         = var.region
  container_image                = var.container_image
  model_name                     = var.model_name
  model_revision                 = var.model_revision
  gpu_type                       = var.gpu_type
  gpu_count                      = var.gpu_count
  allowed_cidr                   = var.allowed_cidr
  max_cost_usd                   = var.max_cost_usd
  timeout_minutes                = var.timeout_minutes
  expires_at_utc                 = var.expires_at_utc
  owner_token                    = var.owner_token
  trace_id                       = var.trace_id
  min_replicas                   = var.min_replicas
  max_replicas                   = var.max_replicas
  http_concurrent_requests       = var.http_concurrent_requests
  vllm_context_length            = var.vllm_context_length
  vllm_max_num_seqs              = var.vllm_max_num_seqs
  vllm_gpu_memory_utilization    = var.vllm_gpu_memory_utilization
  vllm_enforce_eager             = var.vllm_enforce_eager
  vllm_enable_prefix_caching     = var.vllm_enable_prefix_caching
  vllm_enable_chunked_prefill    = var.vllm_enable_chunked_prefill
  vllm_kv_cache_dtype            = var.vllm_kv_cache_dtype
  vllm_quantization              = var.vllm_quantization
}
