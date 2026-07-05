output "compute_instance_id" {
  description = "Azure VM resource id of the deployed compute instance."
  value       = azurerm_linux_virtual_machine.inference.id
}

output "compute_public_ip" {
  description = "Private IP address of the inference server NIC."
  value       = azurerm_network_interface.inference.private_ip_address
}
