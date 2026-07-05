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

module "network" {
  source = "../../modules/network"

  cloud             = "gcp"
  vllm_port         = 8000
  vllm_allowed_cidr = var.allowed_cidr
  ssh_cidr          = var.ssh_cidr
  name_prefix       = "gcp-vllm"
}

module "gpu_cost_watchdog" {
  source = "../../modules/gpu-cost-watchdog"

  max_cost_usd          = var.max_cost_usd
  timeout_minutes       = var.timeout_minutes
  poll_interval_seconds = var.poll_interval_seconds
  region                = var.region
  cloud                 = "gcp"
}

module "vllm_server" {
  source = "../../modules/vllm-server"

  image           = var.image
  gpus            = var.gpus
  model           = var.model
  region          = var.region
  instance_type   = var.instance_type
  extra_args      = var.extra_args
  max_cost_usd    = var.max_cost_usd
  timeout_minutes = var.timeout_minutes
}

resource "google_compute_instance" "inference" {
  name         = "gcp-vllm-01"
  machine_type = var.instance_type
  zone         = "${var.region}-a"

  tags = ["gcp-vllm"]

  boot_disk {
    initialize_params {
      image = var.boot_image
    }
  }

  network_interface {
    network = "default"
    access_config {}
  }

  metadata_startup_script = module.vllm_server.user_data

  scheduling {
    preemptible       = var.use_spot
    automatic_restart = !var.use_spot
  }
}

output "security_group_id" {
  description = "Self-link of the GCP firewall rule created by the network module."
  value       = module.network.security_group_id
}

output "watchdog_user_data" {
  description = "Cloud-init fragment from the gpu-cost-watchdog module. Compose with module.vllm_server.user_data via cloud-init multipart merge at apply time."
  value       = module.gpu_cost_watchdog.user_data
}
