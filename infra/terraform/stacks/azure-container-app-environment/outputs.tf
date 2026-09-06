output "environment_id" {
  description = "Exact ARM ID of the Gludd-owned managed environment."
  value       = module.environment.environment_id
}

output "cleanup_boundary" {
  description = "Exact environment resource ID that the owned Terraform state may destroy."
  value       = module.environment.cleanup_boundary
}
