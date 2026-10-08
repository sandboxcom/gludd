output "environment_id" {
  description = "Exact ARM ID of the Gludd-owned managed environment."
  value       = azapi_resource.managed_environment.id
}

output "cleanup_boundary" {
  description = "Exact environment resource ID that the owned Terraform state may destroy."
  value       = azapi_resource.managed_environment.id
}
