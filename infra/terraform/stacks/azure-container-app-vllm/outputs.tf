output "instance_id" {
  description = "Azure resource ID of the Container App inference service."
  value       = module.vllm_server.instance_id
}

output "base_url" {
  description = "OpenAI-compatible vLLM endpoint."
  value       = module.vllm_server.base_url
}

output "instance_ip" {
  description = "Legacy DeploymentManager identifier alias."
  value       = module.vllm_server.instance_id
}

output "endpoint_url" {
  description = "Legacy DeploymentManager endpoint alias."
  value       = module.vllm_server.base_url
}

output "cleanup_boundary" {
  description = "Exact app-only resource ID used by deterministic cleanup."
  value       = module.vllm_server.cleanup_boundary
}

output "workload_profile_type" {
  description = "Azure Container Apps GPU workload profile used by the service."
  value       = module.vllm_server.workload_profile_type
}

output "revision_name" {
  description = "Azure-generated ready revision bound to the live candidate identity."
  value       = module.vllm_server.revision_name
}

output "watchdog_user_data" {
  description = "Rendered bounded-cost and TTL watchdog artifact for plan evidence."
  value       = module.gpu_cost_watchdog.user_data
}
