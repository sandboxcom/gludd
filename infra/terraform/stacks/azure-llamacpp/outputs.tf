output "instance_id" {
  description = "Azure VM resource id of the deployed inference server."
  value       = azurerm_linux_virtual_machine.inference.id
}

output "public_ip" {
  description = "Private IP address of the inference server NIC."
  value       = azurerm_network_interface.inference.private_ip_address
}

output "base_url" {
  description = "OpenAI-compatible base URL of the inference server."
  value       = module.vllm_server.base_url
}
