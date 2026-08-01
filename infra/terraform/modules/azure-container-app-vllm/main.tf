terraform {
  required_providers {
    azapi = {
      source = "Azure/azapi"
    }
    azurerm = {
      source = "hashicorp/azurerm"
    }
  }
}

locals {
  gpu_profiles = {
    t4 = {
      workload_profile_type = "Consumption-GPU-NC8as-T4"
      cpu                   = 8
      memory                = "56Gi"
    }
    a100_40 = {
      workload_profile_type = "Consumption-GPU-NC24-A100"
      cpu                   = 24
      memory                = "220Gi"
    }
    a100_80 = {
      workload_profile_type = "Consumption-GPU-NC24-A100"
      cpu                   = 24
      memory                = "220Gi"
    }
  }

  selected_profile = local.gpu_profiles[var.gpu_type]
  gpu_profile_type = local.selected_profile.workload_profile_type
  name_suffix      = substr(lower(replace(var.deployment_name, "_", "-")), 0, 32)
  vllm_args = concat(
    ["--model", var.model_name, "--host", "0.0.0.0", "--port", "8000"],
    var.vllm_context_length > 0 ? ["--max-model-len", tostring(var.vllm_context_length)] : [],
    var.vllm_max_num_seqs > 0 ? ["--max-num-seqs", tostring(var.vllm_max_num_seqs)] : [],
    ["--gpu-memory-utilization", tostring(var.vllm_gpu_memory_utilization)],
    var.vllm_enforce_eager ? ["--enforce-eager"] : [],
    var.vllm_enable_prefix_caching ? ["--enable-prefix-caching"] : [],
    var.vllm_enable_chunked_prefill ? ["--enable-chunked-prefill"] : [],
    var.vllm_kv_cache_dtype != "" ? ["--kv-cache-dtype", var.vllm_kv_cache_dtype] : [],
    var.vllm_quantization != "" ? ["--quantization", var.vllm_quantization] : [],
  )
}

resource "azurerm_resource_group" "gludd" {
  name     = "gludd-gpu-${local.name_suffix}"
  location = var.region

  tags = {
    managed-by       = "gludd"
    deployment       = var.deployment_name
    model            = var.model_name
    max-cost-usd     = tostring(var.max_cost_usd)
    timeout-minutes  = tostring(var.timeout_minutes)
    scale-to-zero    = "true"
    workload-profile = local.gpu_profile_type
  }
}

// AzureRM 4.81 still serializes minimumCount and maximumCount as zero for
// Consumption-GPU profiles even when those fields are absent from HCL. Azure
// rejects both properties for serverless GPU environments. AzAPI owns only
// this resource so the request body can omit the unsupported fields entirely.
resource "azapi_resource" "gludd_environment" {
  type      = "Microsoft.App/managedEnvironments@2025-01-01"
  name      = "gludd-cae-${local.name_suffix}"
  parent_id = azurerm_resource_group.gludd.id
  location  = azurerm_resource_group.gludd.location

  body = {
    properties = {
      workloadProfiles = [
        {
          name                = "gludd-gpu"
          workloadProfileType = local.gpu_profile_type
        }
      ]
    }
  }

  tags = azurerm_resource_group.gludd.tags
}

resource "azurerm_container_app" "vllm" {
  name                         = "gludd-vllm-${local.name_suffix}"
  resource_group_name          = azurerm_resource_group.gludd.name
  container_app_environment_id = azapi_resource.gludd_environment.id
  workload_profile_name        = "gludd-gpu"
  revision_mode                = "Single"

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name   = "vllm-server"
      image  = var.container_image
      cpu    = local.selected_profile.cpu
      memory = local.selected_profile.memory
      args   = local.vllm_args
    }

    http_scale_rule {
      name                = "inference-requests"
      concurrent_requests = 1
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    dynamic "ip_security_restriction" {
      for_each = {
        for index, cidr in split(",", var.allowed_cidr) : tostring(index) => trimspace(cidr)
      }
      content {
        action           = "Allow"
        description      = "Explicitly allowed Gludd inference client"
        ip_address_range = ip_security_restriction.value
        name             = "gludd-client-${ip_security_restriction.key}"
      }
    }

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  tags = azurerm_resource_group.gludd.tags
}
