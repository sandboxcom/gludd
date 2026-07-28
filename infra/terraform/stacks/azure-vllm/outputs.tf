output "compute_instance_id" {
  description = "Azure VM resource id of the deployed compute instance."
  value       = azurerm_linux_virtual_machine.inference.id
}

output "compute_public_ip" {
  description = "Public IP address of the inference server."
  value       = azurerm_public_ip.inference.ip_address
}
