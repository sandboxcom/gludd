terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
  skip_provider_registration = true
}

module "vllm_server" {
  source = "./modules/azure-container-app-vllm"

  deployment_name                = var.deployment_name
  region                         = var.region
  container_image                = var.container_image
  model_name                     = var.model_name
  gpu_type                       = var.gpu_type
  gpu_count                      = var.gpu_count
  allowed_cidr                   = var.allowed_cidr
  max_cost_usd                   = var.max_cost_usd
  timeout_minutes                = var.timeout_minutes
  vllm_context_length            = var.vllm_context_length
  vllm_max_num_seqs              = var.vllm_max_num_seqs
  vllm_gpu_memory_utilization    = var.vllm_gpu_memory_utilization
  vllm_enforce_eager             = var.vllm_enforce_eager
  vllm_enable_prefix_caching     = var.vllm_enable_prefix_caching
  vllm_enable_chunked_prefill    = var.vllm_enable_chunked_prefill
  vllm_kv_cache_dtype            = var.vllm_kv_cache_dtype
  vllm_quantization              = var.vllm_quantization
}
