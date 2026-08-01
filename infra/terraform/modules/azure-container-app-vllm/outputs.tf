output "instance_id" {
  description = "Azure resource id of the vLLM Container App."
  value       = azurerm_container_app.vllm.id
}

output "base_url" {
  description = "Root HTTPS URL for the vLLM service; clients append OpenAI API paths."
  value       = "https://${azurerm_container_app.vllm.latest_revision_fqdn}"
}

output "resource_group_name" {
  description = "Resource group deleted during deterministic teardown."
  value       = azurerm_resource_group.gludd.name
}

output "workload_profile_type" {
  description = "Azure GPU workload profile selected from gpu_type."
  value       = local.gpu_profile_type
}
