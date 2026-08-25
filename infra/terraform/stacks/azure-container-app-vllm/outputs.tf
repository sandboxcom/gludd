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

output "resource_group_name" {
  description = "Single Terraform-owned cleanup boundary."
  value       = module.vllm_server.resource_group_name
}

output "workload_profile_type" {
  description = "Azure Container Apps GPU workload profile used by the service."
  value       = module.vllm_server.workload_profile_type
}

output "watchdog_user_data" {
  description = "Rendered bounded-cost and TTL watchdog artifact for plan evidence."
  value       = module.gpu_cost_watchdog.user_data
}
