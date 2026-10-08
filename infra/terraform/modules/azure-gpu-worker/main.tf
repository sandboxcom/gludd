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

resource "azurerm_public_ip" "worker" {
  name                = "${local.resource_prefix}-ip"
  resource_group_name = var.resource_group_name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = var.availability_zone == null ? null : [var.availability_zone]
  tags                = local.common_tags
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

resource "azurerm_network_interface" "worker" {
  name                           = "${local.resource_prefix}-nic"
  resource_group_name            = var.resource_group_name
  location                       = var.location
  accelerated_networking_enabled = var.accelerated_networking_enabled
  tags                           = local.common_tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.worker.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.worker.id
  }
}

resource "azurerm_network_interface_security_group_association" "worker" {
  network_interface_id      = azurerm_network_interface.worker.id
  network_security_group_id = azurerm_network_security_group.worker.id
}

resource "azurerm_linux_virtual_machine" "worker" {
  name                            = "${local.resource_prefix}-vm"
  computer_name                   = local.resource_prefix
  resource_group_name             = var.resource_group_name
  location                        = var.location
  zone                            = var.availability_zone
  size                            = var.vm_size
  admin_username                  = var.admin_username
  disable_password_authentication = true
  allow_extension_operations      = false
  provision_vm_agent              = true
  network_interface_ids           = [azurerm_network_interface.worker.id]
  priority                        = var.use_spot ? "Spot" : "Regular"
  eviction_policy                 = var.use_spot ? "Delete" : null
  max_bid_price                   = var.use_spot ? var.max_spot_price : null
  tags                            = local.common_tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    name                 = "${local.resource_prefix}-osdisk"
    caching              = "ReadWrite"
    storage_account_type = var.os_disk_storage_account_type
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = var.image_publisher
    offer     = var.image_offer
    sku       = var.image_sku
    version   = var.image_version
  }

  boot_diagnostics {}

  timeouts {
    create = "45m"
    read   = "5m"
    update = "45m"
    delete = "45m"
  }

  lifecycle {
    precondition {
      condition = endswith(
        lower(var.resource_group_id),
        "/resourcegroups/${lower(var.resource_group_name)}",
      )
      error_message = "resource_group_name must identify the existing resource-group ID."
    }
  }
}
