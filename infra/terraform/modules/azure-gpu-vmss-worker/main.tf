terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.55"
    }
  }
}

locals {
  resource_prefix = var.deployment_name
  common_tags = merge(var.tags, {
    managed-by       = "gludd"
    deployment       = var.deployment_name
    accelerator-sku  = var.vm_size
    gludd-owner      = var.owner_token
    gludd-trace-id   = var.trace_id
    gludd-expires-at = var.expires_at_utc
    gludd-lifecycle  = "ephemeral"
    orchestration    = "uniform"
  })
  application_health_settings = jsonencode({
    protocol          = "http"
    port              = var.health_port
    requestPath       = var.health_path
    intervalInSeconds = var.health_interval_seconds
    numberOfProbes    = var.health_probe_count
    gracePeriod       = var.health_grace_period_seconds
  })
}

resource "azurerm_virtual_network" "worker" {
  name                = "${local.resource_prefix}-vnet"
  address_space       = [var.vnet_address_space]
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = local.common_tags
}

resource "azurerm_subnet" "worker" {
  name                 = "accelerator"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.worker.name
  address_prefixes     = [var.subnet_address_prefix]
}

resource "azurerm_network_security_group" "worker" {
  name                = "${local.resource_prefix}-nsg"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = local.common_tags

  security_rule {
    name                       = "controller-ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.controller_cidr
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "controller-inference"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = tostring(var.inference_port)
    source_address_prefix      = var.controller_cidr
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "worker" {
  subnet_id                 = azurerm_subnet.worker.id
  network_security_group_id = azurerm_network_security_group.worker.id
}

resource "azurerm_public_ip" "egress" {
  name                = "${local.resource_prefix}-egress-ip"
  resource_group_name = var.resource_group_name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.common_tags
}

resource "azurerm_nat_gateway" "worker" {
  name                    = "${local.resource_prefix}-nat"
  resource_group_name     = var.resource_group_name
  location                = var.location
  sku_name                = "Standard"
  idle_timeout_in_minutes = var.egress_idle_timeout_minutes
  tags                    = local.common_tags
}

resource "azurerm_nat_gateway_public_ip_association" "worker" {
  nat_gateway_id       = azurerm_nat_gateway.worker.id
  public_ip_address_id = azurerm_public_ip.egress.id
}

resource "azurerm_subnet_nat_gateway_association" "worker" {
  subnet_id      = azurerm_subnet.worker.id
  nat_gateway_id = azurerm_nat_gateway.worker.id
}

resource "azurerm_linux_virtual_machine_scale_set" "worker" {
  name                            = "${local.resource_prefix}-vmss"
  computer_name_prefix            = local.resource_prefix
  resource_group_name             = var.resource_group_name
  location                        = var.location
  sku                             = var.vm_size
  instances                       = var.instance_count
  zones                           = var.availability_zones
  zone_balance                    = length(var.availability_zones) > 1
  platform_fault_domain_count     = var.platform_fault_domain_count
  single_placement_group          = var.rdma_enabled ? true : var.single_placement_group
  admin_username                  = var.admin_username
  disable_password_authentication = true
  provision_vm_agent              = true
  extension_operations_enabled    = true
  extensions_time_budget          = "PT30M"
  overprovision                   = false
  upgrade_mode                    = "Rolling"
  priority                        = var.use_spot ? "Spot" : "Regular"
  eviction_policy                 = var.use_spot ? "Delete" : null
  max_bid_price                   = var.use_spot ? var.max_spot_price : null
  tags                            = local.common_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.user_assigned_identity_id]
  }

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = var.os_disk_storage_account_type
    disk_size_gb         = var.os_disk_size_gb
  }

  dynamic "data_disk" {
    for_each = var.cache_disk_enabled ? [1] : []
    content {
      lun                  = 0
      caching              = "ReadOnly"
      create_option        = "Empty"
      storage_account_type = var.cache_disk_storage_account_type
      disk_size_gb         = var.cache_disk_size_gb
    }
  }

  source_image_reference {
    publisher = var.image_publisher
    offer     = var.image_offer
    sku       = var.image_sku
    version   = var.image_version
  }

  network_interface {
    name                          = "primary"
    primary                       = true
    enable_accelerated_networking = var.accelerated_networking_enabled

    ip_configuration {
      name      = "primary"
      primary   = true
      subnet_id = azurerm_subnet.worker.id
    }
  }

  extension {
    name                       = "ApplicationHealthLinux"
    publisher                  = "Microsoft.ManagedServices"
    type                       = "ApplicationHealthLinux"
    type_handler_version       = "2.0"
    auto_upgrade_minor_version = true
    settings                   = local.application_health_settings
  }

  rolling_upgrade_policy {
    max_batch_instance_percent              = var.max_batch_percent
    max_unhealthy_instance_percent          = 0
    max_unhealthy_upgraded_instance_percent = 0
    pause_time_between_batches              = var.pause_between_batches
    prioritize_unhealthy_instances_enabled  = false
    maximum_surge_instances_enabled         = true
  }

  automatic_instance_repair {
    enabled      = true
    grace_period = var.repair_grace_period
  }

  termination_notification {
    enabled = true
    timeout = "PT15M"
  }

  boot_diagnostics {}

  timeouts {
    create = "90m"
    read   = "5m"
    update = "90m"
    delete = "90m"
  }

  lifecycle {
    precondition {
      condition = endswith(
        lower(var.resource_group_id),
        "/resourcegroups/${lower(var.resource_group_name)}",
      )
      error_message = "resource_group_name must identify the existing resource-group ID."
    }

    precondition {
      condition     = !var.cache_disk_enabled || var.cache_price_attested
      error_message = "cache disks require independent live price attestation."
    }

    precondition {
      condition     = !var.rdma_enabled || length(var.availability_zones) <= 1
      error_message = "RDMA VMSS workers must remain in one attested zone and placement group."
    }
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.worker,
    azurerm_subnet_network_security_group_association.worker,
  ]
}
