terraform {
  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.7"
    }
  }
}

provider "libvirt" {
  uri = var.libvirt_uri
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

module "gpu_cost_watchdog" {
  source = "../../modules/gpu-cost-watchdog"

  max_cost_usd    = var.max_cost_usd
  timeout_minutes = var.timeout_minutes
  region          = var.region
  cloud           = "qemu"
}

module "qemu_vm" {
  source = "../../modules/qemu-vm"

  name                  = var.name
  vcpus                 = var.vcpus
  memory_mb             = var.memory_mb
  disk_size_gb          = var.disk_size_gb
  base_image_url        = var.base_image_url
  storage_pool          = var.storage_pool
  network_name          = var.network_name
  host_port             = var.host_port
  ssh_public_key        = var.ssh_public_key
  cloud_init_user_data  = "${module.gpu_cost_watchdog.user_data}\n${module.vllm_server.user_data}"
}
