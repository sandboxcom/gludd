# Outputs for the network module (Phase 1 stub).

output "allowed_cidr" {
  description = "Pass-through of var.allowed_cidr."
  value       = var.allowed_cidr
}

output "inference_port" {
  description = "Pass-through of var.inference_port."
  value       = var.inference_port
}
