output "virtual_machine_id" {
  description = "Exact ARM ID of the single OpenTofu-owned GPU worker VM."
  value       = azurerm_linux_virtual_machine.worker.id
}

output "os_disk_id" {
  description = "Exact ARM ID of the VM-owned OS disk for cleanup attestation."
  value       = azurerm_linux_virtual_machine.worker.os_disk[0].id
}

output "network_interface_id" {
  description = "Exact ARM ID of the worker network interface."
  value       = azurerm_network_interface.worker.id
}

output "public_ip_address" {
  description = "Ephemeral worker address restricted to the controller /32."
  value       = azurerm_public_ip.worker.ip_address
}

output "ansible_host" {
  description = "Public address passed to the separate Ansible bootstrap and attestation phase."
  value       = azurerm_public_ip.worker.ip_address
}

output "ansible_user" {
  description = "Unprivileged SSH account passed to the Ansible inventory."
  value       = var.admin_username
}

output "inference_endpoint" {
  description = "Controller-restricted endpoint populated only after Ansible attests the runner."
  value       = "http://${azurerm_public_ip.worker.ip_address}:${var.inference_port}"
}

output "owned_resource_ids" {
  description = "Complete child-resource cleanup boundary; the existing resource group is excluded."
  value = [
    azurerm_linux_virtual_machine.worker.id,
    azurerm_linux_virtual_machine.worker.os_disk[0].id,
    azurerm_network_interface.worker.id,
    azurerm_network_interface_security_group_association.worker.id,
    azurerm_network_security_group.worker.id,
    azurerm_public_ip.worker.id,
    azurerm_subnet.worker.id,
    azurerm_virtual_network.worker.id,
  ]
}
