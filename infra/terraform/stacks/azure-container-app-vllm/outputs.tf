output "instance_id" {
  value = module.vllm_server.instance_id
}

output "base_url" {
  value = module.vllm_server.base_url
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
  value = module.vllm_server.resource_group_name
}

output "workload_profile_type" {
  value = module.vllm_server.workload_profile_type
}
