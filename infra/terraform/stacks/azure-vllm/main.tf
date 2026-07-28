terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
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

locals {
  common_tags = {
    managed-by      = "gludd"
    deployment      = var.deployment_name
    accelerator-sku = var.instance_type
    zero-downtime   = "terraform-destroy"
  }

  accelerator_cloud_init = <<-CLOUD_INIT
    #!/bin/bash
    set -euxo pipefail
    install -d -m 0755 /var/log/gludd /var/lib/gludd
    exec > >(tee -a /var/log/gludd/accelerator-bootstrap.log) 2>&1

    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl docker.io gnupg

    # The Microsoft.HpcCompute extension installs the host driver separately.
    # Wait for it before configuring the NVIDIA container runtime.
    for attempt in $(seq 1 120); do
      if nvidia-smi >/dev/null 2>&1; then
        break
      fi
      if [ "$attempt" -eq 120 ]; then
        echo "NVIDIA driver did not become ready" >&2
        exit 1
      fi
      sleep 10
    done

    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
    apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker

    cat >/usr/local/bin/gludd-accelerator-serve <<'SERVE'
    #!/bin/bash
    set -euo pipefail
    exec ${module.vllm_server.serve_command}
    SERVE
    chmod 0755 /usr/local/bin/gludd-accelerator-serve

    cat >/etc/systemd/system/gludd-accelerator.service <<'UNIT'
    [Unit]
    Description=Gludd vLLM Azure accelerator service
    After=docker.service network-online.target
    Requires=docker.service

    [Service]
    Type=simple
    ExecStart=/usr/local/bin/gludd-accelerator-serve
    Restart=on-failure
    RestartSec=10

    [Install]
    WantedBy=multi-user.target
    UNIT

    cat >/usr/local/bin/gludd-gpu-metrics <<'METRICS'
    #!/bin/bash
    set -euo pipefail
    printf '{"timestamp":"%s","gpu":"' "$(date -u +%FT%TZ)"
    nvidia-smi --query-gpu=name,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw \
      --format=csv,noheader,nounits | tr '\n' ';'
    printf '"}\n'
    METRICS
    chmod 0755 /usr/local/bin/gludd-gpu-metrics

    cat >/etc/systemd/system/gludd-gpu-metrics.service <<'METRICS_UNIT'
    [Unit]
    Description=Gludd GPU metrics snapshot

    [Service]
    Type=oneshot
    ExecStart=/bin/sh -c '/usr/local/bin/gludd-gpu-metrics >>/var/log/gludd/gpu-metrics.jsonl'
    METRICS_UNIT

    cat >/etc/systemd/system/gludd-gpu-metrics.timer <<'TIMER'
    [Unit]
    Description=Collect Gludd GPU metrics every minute

    [Timer]
    OnBootSec=1min
    OnUnitActiveSec=1min
    Unit=gludd-gpu-metrics.service

    [Install]
    WantedBy=timers.target
    TIMER

    nvidia-smi --query-gpu=name,uuid,memory.total --format=csv,noheader \
      >/var/lib/gludd/accelerator-ready.csv
    systemctl daemon-reload
    systemctl enable --now gludd-accelerator.service
    systemctl enable --now gludd-gpu-metrics.timer

    for attempt in $(seq 1 180); do
      if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
        touch /var/lib/gludd/inference-ready
        exit 0
      fi
      sleep 10
    done
    echo "Inference health endpoint did not become ready" >&2
    exit 1
  CLOUD_INIT
}

resource "azurerm_resource_group" "inference" {
  name     = var.deployment_name
  location = var.region
  tags     = local.common_tags
}

resource "azurerm_virtual_network" "inference" {
  name                = "${var.deployment_name}-vnet"
  address_space       = ["10.42.0.0/16"]
  location            = azurerm_resource_group.inference.location
  resource_group_name = azurerm_resource_group.inference.name
  tags                = local.common_tags
}

resource "azurerm_subnet" "inference" {
  name                 = "accelerator"
  resource_group_name  = azurerm_resource_group.inference.name
  virtual_network_name = azurerm_virtual_network.inference.name
  address_prefixes     = ["10.42.1.0/24"]
}

resource "azurerm_public_ip" "inference" {
  name                = "${var.deployment_name}-ip"
  resource_group_name = azurerm_resource_group.inference.name
  location            = azurerm_resource_group.inference.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.common_tags
}

resource "azurerm_network_security_group" "inference" {
  name                = "${var.deployment_name}-nsg"
  resource_group_name = azurerm_resource_group.inference.name
  location            = azurerm_resource_group.inference.location
  tags                = local.common_tags
}

resource "azurerm_network_security_rule" "inference" {
  name                        = "allow-inference"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "8000"
  source_address_prefix       = var.allowed_cidr
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.inference.name
  network_security_group_name = azurerm_network_security_group.inference.name
}

resource "azurerm_network_security_rule" "ssh" {
  name                        = "allow-ssh"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = var.allowed_cidr
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.inference.name
  network_security_group_name = azurerm_network_security_group.inference.name
}

resource "azurerm_network_interface" "inference" {
  name                           = "${var.deployment_name}-nic"
  location                       = azurerm_resource_group.inference.location
  resource_group_name            = azurerm_resource_group.inference.name
  accelerated_networking_enabled = true
  tags                           = local.common_tags

  ip_configuration {
    name                          = "inference"
    subnet_id                     = azurerm_subnet.inference.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.inference.id
  }
}

resource "azurerm_network_interface_security_group_association" "inference" {
  network_interface_id      = azurerm_network_interface.inference.id
  network_security_group_id = azurerm_network_security_group.inference.id
}

resource "azurerm_linux_virtual_machine" "inference" {
  name                  = "${var.deployment_name}-vm"
  resource_group_name   = azurerm_resource_group.inference.name
  location              = azurerm_resource_group.inference.location
  size                  = var.instance_type
  admin_username        = "azureuser"
  network_interface_ids = [azurerm_network_interface.inference.id]
  priority              = var.use_spot ? "Spot" : "Regular"
  eviction_policy       = var.use_spot ? "Delete" : null
  max_bid_price         = var.use_spot ? -1 : null
  custom_data           = base64encode(module.gpu_cost_watchdog.user_data)
  tags                  = local.common_tags

  admin_ssh_key {
    username   = "azureuser"
    public_key = file(pathexpand(var.ssh_public_key_path))
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
    disk_size_gb         = var.disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  boot_diagnostics {}
}

resource "azurerm_virtual_machine_extension" "nvidia_driver" {
  name                       = "NvidiaGpuDriverLinux"
  virtual_machine_id         = azurerm_linux_virtual_machine.inference.id
  publisher            = "Microsoft.HpcCompute"
  type                 = "NvidiaGpuDriverLinux"
  type_handler_version       = "1.6"
  auto_upgrade_minor_version = true
  automatic_upgrade_enabled  = true
  tags                       = local.common_tags
}

resource "azurerm_virtual_machine_extension" "accelerator_bootstrap" {
  name                       = "GluddAcceleratorBootstrap"
  virtual_machine_id         = azurerm_linux_virtual_machine.inference.id
  publisher                  = "Microsoft.Azure.Extensions"
  type                       = "CustomScript"
  type_handler_version       = "2.1"
  auto_upgrade_minor_version = true
  tags                       = local.common_tags

  protected_settings = jsonencode({
    commandToExecute = "echo '${base64encode(local.accelerator_cloud_init)}' | base64 -d >/tmp/gludd-accelerator-bootstrap.sh && bash /tmp/gludd-accelerator-bootstrap.sh"
  })

  depends_on = [azurerm_virtual_machine_extension.nvidia_driver]
}

output "instance_id" {
  description = "Azure resource id of the accelerator VM."
  value       = azurerm_linux_virtual_machine.inference.id
}

output "deployment_id" {
  description = "URL-safe deployment registry identifier."
  value       = var.deployment_name
}

output "instance_ip" {
  description = "Public IP used by gludd to register the inference endpoint."
  value       = azurerm_public_ip.inference.ip_address
}

output "base_url" {
  description = "OpenAI-compatible vLLM endpoint."
  value       = "http://${azurerm_public_ip.inference.ip_address}:8000/v1"
}

output "endpoint_url" {
  description = "Compatibility alias consumed by DeploymentManager."
  value       = "http://${azurerm_public_ip.inference.ip_address}:8000/v1"
}

output "resource_group_name" {
  description = "Single Terraform-owned cleanup boundary."
  value       = azurerm_resource_group.inference.name
}

output "driver_extension_id" {
  description = "Azure NVIDIA driver extension evidence."
  value       = azurerm_virtual_machine_extension.nvidia_driver.id
}

output "bootstrap_extension_id" {
  description = "Driver-ready inference bootstrap and hardware-smoke evidence."
  value       = azurerm_virtual_machine_extension.accelerator_bootstrap.id
}
