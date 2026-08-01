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

module "network" {
  source = "../../modules/network"

  cloud                = "azure"
  vllm_port            = 8000
  vllm_allowed_cidr    = var.allowed_cidr
  ssh_cidr             = var.ssh_cidr
  name_prefix          = "azure-vllm"
  region               = var.region
  azure_resource_group = var.azure_resource_group
}

module "gpu_cost_watchdog" {
  source = "../../modules/gpu-cost-watchdog"

  max_cost_usd          = var.max_cost_usd
  timeout_minutes       = var.timeout_minutes
  poll_interval_seconds = var.poll_interval_seconds
  region                = var.region
  cloud                 = "azure"
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

resource "azurerm_network_interface" "inference" {
  name                = "azure-vllm-nic"
  location            = var.region
  resource_group_name = var.azure_resource_group

  ip_configuration {
    name                          = "internal"
    subnet_id                     = var.subnet_id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "inference" {
  name                  = "azure-vllm-01"
  resource_group_name   = var.azure_resource_group
  location              = var.region
  size                  = var.instance_type
  admin_username        = "azureuser"
  network_interface_ids = [azurerm_network_interface.inference.id]

  admin_ssh_key {
    username   = "azureuser"
    public_key = file("~/.ssh/id_rsa.pub")
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts"
    version   = "latest"
  }

  custom_data = base64encode(module.vllm_server.user_data)

  priority        = var.use_spot ? "Spot" : "Regular"
  eviction_policy = var.use_spot ? "Delete" : null
}

output "instance_id" {
  description = "Provider-assigned instance id of the deployed inference server."
  value       = module.vllm_server.instance_id
}

output "base_url" {
  description = "OpenAI-compatible base URL of the inference server."
  value       = module.vllm_server.base_url
}

output "security_group_id" {
  description = "Id of the Azure NSG created by the network module."
  value       = module.network.security_group_id
}

output "watchdog_user_data" {
  description = "Cloud-init fragment from the gpu-cost-watchdog module. Compose with module.vllm_server.user_data via cloud-init multipart merge at apply time."
  value       = module.gpu_cost_watchdog.user_data
}
