terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  region = var.region
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
