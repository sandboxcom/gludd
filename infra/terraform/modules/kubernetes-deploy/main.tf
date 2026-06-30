locals {
  common_labels = merge(
    {
      "app.kubernetes.io/name"       = "inference-server"
      "app.kubernetes.io/engine"     = var.engine
      "app.kubernetes.io/managed-by" = "terraform"
    },
    var.labels,
  )

  container_port = var.service_port

  vllm_args = [
    "--model", var.model_name,
    "--host", "0.0.0.0",
    "--port", tostring(var.service_port),
  ]

  llamacpp_args = [
    "-m", var.model_name,
    "--host", "0.0.0.0",
    "--port", tostring(var.service_port),
  ]

  base_args = var.engine == "vllm" ? local.vllm_args : local.llamacpp_args

  extra_args_list = var.extra_args != "" ? split(" ", var.extra_args) : []

  container_args = concat(local.base_args, local.extra_args_list)

  serve_command = var.engine == "vllm" ? "vllm serve --model ${var.model_name} --host 0.0.0.0 --port ${var.service_port} ${var.extra_args}" : "llamacpp serve -m ${var.model_name} --host 0.0.0.0 --port ${var.service_port} ${var.extra_args}"

  gpu_requests = var.gpu_count > 0 ? { "${var.gpu_vendor}" = tostring(var.gpu_count) } : {}
  gpu_limits   = var.gpu_count > 0 ? { "${var.gpu_vendor}" = tostring(var.gpu_count) } : {}

  resource_requests = merge(
    {
      cpu    = var.cpu_request
      memory = var.memory_request
    },
    local.gpu_requests,
  )

  resource_limits = merge(
    {
      cpu    = var.cpu_limit
      memory = var.memory_limit
    },
    local.gpu_limits,
  )
}

resource "kubernetes_config_map_v1" "server_config" {
  metadata {
    name      = "${var.engine}-server-config"
    namespace = var.namespace
    labels    = local.common_labels
  }

  data = {
    "engine"        = var.engine
    "model_name"    = var.model_name
    "serve_command" = local.serve_command
  }
}

resource "kubernetes_deployment_v1" "inference_server" {
  metadata {
    name      = "${var.engine}-server"
    namespace = var.namespace
    labels    = local.common_labels
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = local.common_labels
    }

    template {
      metadata {
        labels = local.common_labels
      }

      spec {
        dynamic "volume" {
          for_each = var.model_pvc_name != "" ? [1] : []
          content {
            name = "models"
            persistent_volume_claim {
              claim_name = var.model_pvc_name
              read_only  = true
            }
          }
        }

        container {
          name    = "${var.engine}-server"
          image   = var.image
          args    = local.container_args

          port {
            name           = "http"
            container_port = local.container_port
            protocol       = "TCP"
          }

          resources {
            requests = local.resource_requests
            limits   = local.resource_limits
          }

          dynamic "volume_mount" {
            for_each = var.model_pvc_name != "" ? [1] : []
            content {
              name       = "models"
              mount_path = var.model_mount_path
              read_only  = true
            }
          }

          liveness_probe {
            http_get {
              path = var.health_check_path
              port = local.container_port
            }
            initial_delay_seconds = var.liveness_initial_delay
            period_seconds        = 15
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = var.health_check_path
              port = local.container_port
            }
            initial_delay_seconds = var.readiness_initial_delay
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 3
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "inference_server" {
  metadata {
    name      = "${var.engine}-server"
    namespace = var.namespace
    labels    = local.common_labels
  }

  spec {
    type = var.service_type

    selector = local.common_labels

    port {
      name        = "http"
      port        = var.service_port
      target_port = local.container_port
      protocol    = "TCP"
    }
  }
}
