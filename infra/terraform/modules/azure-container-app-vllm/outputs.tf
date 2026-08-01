output "instance_id" {
  description = "Azure resource id of the vLLM Container App."
  value       = azurerm_container_app.vllm.id
}

output "base_url" {
  description = "OpenAI-compatible vLLM base URL."
  value       = "https://${azurerm_container_app.vllm.latest_revision_fqdn}/v1"
}

output "resource_group_name" {
  description = "Resource group deleted during deterministic teardown."
  value       = azurerm_resource_group.gludd.name
}

output "workload_profile_type" {
  description = "Azure GPU workload profile selected from gpu_type."
  value       = local.gpu_profile_type
}
