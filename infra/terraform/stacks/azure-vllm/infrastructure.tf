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
  destination_address_prefix = "*"
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
  destination_address_prefix = "*"
  resource_group_name         = azurerm_resource_group.inference.name
  network_security_group_name = azurerm_network_security_group.inference.name
}

resource "azurerm_network_interface_security_group_association" "inference" {
  network_interface_id      = azurerm_network_interface.inference.id
  network_security_group_id = azurerm_network_security_group.inference.id
}

resource "azurerm_virtual_machine_extension" "nvidia_driver" {
  name                       = "NvidiaGpuDriverLinux"
  virtual_machine_id         = azurerm_linux_virtual_machine.inference.id
  publisher                  = "Microsoft.HpcCompute"
  type                       = "NvidiaGpuDriverLinux"
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
