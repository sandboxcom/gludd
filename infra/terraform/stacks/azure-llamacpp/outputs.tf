output "compute_instance_id" {
  description = "Azure VM resource id of the deployed compute instance."
  value       = azurerm_linux_virtual_machine.inference.id
}

output "compute_public_ip" {
  description = "Public IP address of the inference server."
  value       = azurerm_public_ip.inference.ip_address
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
  description = "OpenAI-compatible llama.cpp endpoint."
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

output "watchdog_user_data" {
  description = "Rendered GPU cost-watchdog bootstrap for release validation."
  value       = module.gpu_cost_watchdog.user_data
  sensitive   = true
}
