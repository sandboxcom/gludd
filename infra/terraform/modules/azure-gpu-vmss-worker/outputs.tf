output "virtual_machine_scale_set_id" {
  description = "Exact ARM ID of the OpenTofu-owned Uniform GPU worker set."
  value       = azurerm_linux_virtual_machine_scale_set.worker.id
}

output "subnet_id" {
  description = "Private subnet queried by the separate inventory and Ansible phases."
  value       = azurerm_subnet.worker.id
}

output "egress_ip" {
  description = "Stable outbound address for allowlists; no inbound public endpoint is created."
  value       = azurerm_public_ip.egress.ip_address
}

output "ansible_user" {
  description = "Unprivileged account used after ARM inventory resolves private instance addresses."
  value       = var.admin_username
}

output "inference_port" {
  description = "Private runner port restricted to the controller address."
  value       = var.inference_port
}

output "private_instance_inventory_query" {
  description = "Secret-free Azure Resource Graph predicate for resolving current VMSS instances."
  value       = "Resources | where id startswith '${azurerm_linux_virtual_machine_scale_set.worker.id}/virtualMachines/' | project id, name, properties"
}

output "owned_resource_ids" {
  description = "Complete top-level cleanup boundary; implicit VMSS disks and NICs are children."
  value = [
    azurerm_linux_virtual_machine_scale_set.worker.id,
    azurerm_nat_gateway.worker.id,
    azurerm_nat_gateway_public_ip_association.worker.id,
    azurerm_network_security_group.worker.id,
    azurerm_public_ip.egress.id,
    azurerm_subnet.worker.id,
    azurerm_subnet_nat_gateway_association.worker.id,
    azurerm_subnet_network_security_group_association.worker.id,
    azurerm_virtual_network.worker.id,
  ]
}
