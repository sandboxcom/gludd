# Outputs for the gpu-cost-watchdog module.

output "script" {
  description = "Bash shutdown script that enforces MAX_COST / TIMEOUT_MIN. Stack writes this to the instance (e.g. /usr/local/bin/gpu-cost-watchdog.sh) and installs a systemd unit via cloud-init."
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
