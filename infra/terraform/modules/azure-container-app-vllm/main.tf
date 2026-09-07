terraform {
  required_providers {
    azapi = {
      source = "Azure/azapi"
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
    ["--model", var.model_name, "--revision", var.model_revision, "--tokenizer-revision", var.model_revision],
    ["--served-model-name", var.model_name, "--host", "0.0.0.0", "--port", "8000", "--dtype", "half"],
    var.vllm_context_length > 0 ? ["--max-model-len", tostring(var.vllm_context_length)] : [],
    var.vllm_max_num_seqs > 0 ? ["--max-num-seqs", tostring(var.vllm_max_num_seqs)] : [],
    ["--gpu-memory-utilization", tostring(var.vllm_gpu_memory_utilization)],
    var.vllm_enforce_eager ? ["--enforce-eager"] : [],
    var.vllm_enable_prefix_caching ? ["--enable-prefix-caching"] : [],
    var.vllm_enable_chunked_prefill ? ["--enable-chunked-prefill"] : [],
    var.vllm_kv_cache_dtype != "" ? ["--kv-cache-dtype", var.vllm_kv_cache_dtype] : [],
    var.vllm_quantization != "" ? ["--quantization", var.vllm_quantization] : [],
  )
  tags = {
    managed-by       = "gludd"
    deployment       = var.deployment_name
    model            = var.model_name
    model-revision   = var.model_revision
    max-cost-usd     = tostring(var.max_cost_usd)
    timeout-minutes  = tostring(var.timeout_minutes)
    scale-to-zero    = "true"
    workload-profile = local.gpu_profile_type
    gludd-expires-at = var.expires_at_utc
    gludd-owner      = var.owner_token
    gludd-trace-id   = var.trace_id
  }
}

// The one remote resource owned by this module. The resource group and managed
// environment are operator-owned prerequisites and cannot be deleted by this role.
// AzAPI is used because AzureRM reads Container App secrets unconditionally.
resource "azapi_resource" "vllm" {
  type      = "Microsoft.App/containerApps@2025-01-01"
  name      = "gludd-vllm-${local.name_suffix}"
  parent_id = var.resource_group_id
  location  = var.region

  body = {
    properties = {
      managedEnvironmentId = var.managed_environment_id
      workloadProfileName  = var.workload_profile_name
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          allowInsecure = false
          targetPort    = 8000
          transport     = "auto"
          ipSecurityRestrictions = [
            {
              action         = "Allow"
              description    = "Exact Gludd live-proof caller"
              ipAddressRange = var.allowed_cidr
              name           = "gludd-live-proof-client"
            }
          ]
        }
      }
      template = {
        containers = [
          {
            name  = "vllm-server"
            image = var.container_image
            args  = local.vllm_args
            env = [
              {
                name  = "HOME"
                value = "/tmp"
              },
              {
                name  = "HF_HOME"
                value = "/tmp/huggingface"
              },
              {
                name  = "HF_HUB_DISABLE_TELEMETRY"
                value = "1"
              },
              {
                name  = "XDG_CACHE_HOME"
                value = "/tmp/.cache"
              },
              {
                name  = "TORCHINDUCTOR_CACHE_DIR"
                value = "/tmp/torchinductor"
              },
              {
                name  = "VLLM_CONFIG_ROOT"
                value = "/tmp/.config/vllm"
              }
            ]
            resources = {
              cpu    = local.selected_profile.cpu
              memory = local.selected_profile.memory
            }
            probes = [
              {
                type = "Startup"
                httpGet = {
                  path   = "/health"
                  port   = 8000
                  scheme = "HTTP"
                }
                initialDelaySeconds = 10
                periodSeconds       = 10
                timeoutSeconds      = 5
                failureThreshold    = 60
              },
              {
                type = "Readiness"
                httpGet = {
                  path   = "/health"
                  port   = 8000
                  scheme = "HTTP"
                }
                periodSeconds    = 10
                timeoutSeconds   = 5
                failureThreshold = 6
              }
            ]
          }
        ]
        scale = {
          minReplicas = var.min_replicas
          maxReplicas = var.max_replicas
          rules = [
            {
              name = "inference-requests"
              http = {
                metadata = {
                  concurrentRequests = tostring(var.http_concurrent_requests)
                }
              }
            }
          ]
        }
      }
    }
  }

  tags = local.tags
  response_export_values = [
    "properties.configuration.ingress.fqdn",
    "properties.latestReadyRevisionName",
  ]

  lifecycle {
    precondition {
      condition = startswith(
        lower(var.managed_environment_id),
        "${lower(var.resource_group_id)}/providers/microsoft.app/managedenvironments/",
      )
      error_message = "managed_environment_id must identify an environment inside resource_group_id."
    }
    precondition {
      condition     = var.workload_profile_type == local.gpu_profile_type
      error_message = "workload_profile_type must match the selected right-sized GPU profile."
    }
  }
}
