terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.55"
    }
  }
}

provider "azurerm" {
  location = var.region
  features {}
}

module "gpu_cost_watchdog" {
  source = "../../modules/gpu-cost-watchdog"

  max_cost_usd    = var.max_cost_usd
  timeout_minutes = var.timeout_minutes
  region          = var.region
  cloud           = "azure"
}

module "vllm_server" {
  source = "../../modules/llamacpp-server"

  image           = var.image
  gpus            = var.gpus
  model           = var.model
  region          = var.region
  instance_type   = var.instance_type
  extra_args      = var.extra_args
  max_cost_usd    = var.max_cost_usd
  timeout_minutes = var.timeout_minutes
}
