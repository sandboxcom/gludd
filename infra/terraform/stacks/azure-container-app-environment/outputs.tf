output "environment_id" {
  description = "Exact ARM ID of the Gludd-owned managed environment."
  value       = module.environment.environment_id
}

output "cleanup_boundary" {
  description = "Exact environment resource ID that the owned Terraform state may destroy."
  value       = module.environment.cleanup_boundary
}

output "runtime_class" {
  description = "Machine-readable proof that this stack owns control-plane state and launches no model worker."
  value       = "control-plane"
}
