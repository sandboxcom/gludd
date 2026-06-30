# Outputs for the gpu-cost-watchdog module.

output "user_data" {
  description = <<-EOT
  Rendered #cloud-config YAML fragment that writes the watchdog script + systemd
  unit and starts the unit on first boot. Stacks concatenate this with the
  engine module's user_data (e.g. via cloud-init multipart merge).
  EOT
  value       = terraform_data.gpu_cost_watchdog.output.cloud_init
}

output "script_path" {
  description = "Absolute path the rendered script is written to inside the VM."
  value       = terraform_data.gpu_cost_watchdog.output.script_path
}

output "script" {
  description = "Raw bash body of the watchdog script (pre-cloud-init wrapping). Surfaced for logging/diff in plans."
  value       = terraform_data.gpu_cost_watchdog.output.script
}

output "max_cost_usd" {
  description = "Pass-through of var.max_cost_usd for tagging/labeling."
  value       = var.max_cost_usd
}

output "timeout_minutes" {
  description = "Pass-through of var.timeout_minutes for tagging/labeling."
  value       = var.timeout_minutes
}
