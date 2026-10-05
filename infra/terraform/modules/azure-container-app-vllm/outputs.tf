output "instance_id" {
  description = "Azure resource ID of the sole Terraform-owned Container App."
  value       = azapi_resource.vllm.id
}

output "base_url" {
  description = "Root HTTPS URL for the vLLM service; clients append OpenAI API paths."
  value       = "https://${azapi_resource.vllm.output.properties.configuration.ingress.fqdn}"
}

output "cleanup_boundary" {
  description = "Only the Container App is destroyed; it resides in an existing resource group; never deleted by this stack."
  value       = azapi_resource.vllm.id
}

output "workload_profile_type" {
  description = "Azure GPU profile independently selected by Gludd's sizing proof."
  value       = local.gpu_profile_type
}

output "revision_name" {
  description = "Azure-generated ready revision bound to the live candidate identity."
  value       = azapi_resource.vllm.output.properties.latestReadyRevisionName
}
