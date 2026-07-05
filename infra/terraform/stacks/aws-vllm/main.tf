terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# Network: opens var.vllm_port from var.allowed_cidr + 22 from var.ssh_cidr.
module "network" {
  source = "../../modules/network"

  cloud             = "aws"
  vllm_port         = 8000
  vllm_allowed_cidr = var.allowed_cidr
  ssh_cidr          = var.ssh_cidr
  name_prefix       = "aws-vllm"
}

# Self-terminating watchdog: enforces var.max_cost_usd + var.timeout_minutes
# on the deployed VM via cloud-init + systemd.
module "gpu_cost_watchdog" {
  source = "../../modules/gpu-cost-watchdog"

  max_cost_usd          = var.max_cost_usd
  timeout_minutes       = var.timeout_minutes
  poll_interval_seconds = var.poll_interval_seconds
  region                = var.region
  cloud                 = "aws"
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

resource "aws_instance" "inference" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [module.network.security_group_id]
  user_data              = module.vllm_server.user_data

  tags = {
    Name = "aws-vllm-inference"
  }

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        max_price = var.spot_price
      }
    }
  }
}

output "security_group_id" {
  description = "Id of the AWS security group created by the network module."
  value       = module.network.security_group_id
}

output "watchdog_user_data" {
  description = "Cloud-init fragment from the gpu-cost-watchdog module. Compose with module.vllm_server.user_data via cloud-init multipart merge at apply time."
  value       = module.gpu_cost_watchdog.user_data
}
