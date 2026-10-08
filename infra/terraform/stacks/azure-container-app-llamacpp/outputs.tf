output "instance_id" {
  description = "Provider-assigned instance id of the deployed inference server."
  value       = module.vllm_server.instance_id
}

output "base_url" {
  description = "OpenAI-compatible base URL of the inference server."
  value       = module.vllm_server.base_url
}

output "watchdog_user_data" {
  description = "Rendered bounded-cost and TTL watchdog artifact for plan evidence."
  value       = module.gpu_cost_watchdog.user_data
}
