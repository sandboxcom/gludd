terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.31"
    }
  }
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kubeconfig_context
}

module "kubernetes_deploy" {
  source = "../../modules/kubernetes-deploy"

  image        = var.image
  model_name   = var.model
  engine       = "llamacpp"
  namespace    = var.namespace
  replicas     = var.replicas
  gpu_count    = var.gpu_count
  cpu_request  = var.cpu_request
  memory_request = var.memory_request
  cpu_limit    = var.cpu_limit
  memory_limit = var.memory_limit
  gpu_vendor   = var.gpu_vendor
  model_pvc_name   = var.model_pvc_name
  model_mount_path = var.model_mount_path
  extra_args   = var.extra_args
  service_type = var.service_type
  service_port = var.service_port
}
