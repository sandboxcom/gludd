output "instance_id" {
  description = "Provider-assigned instance id of the deployed inference server."
  value       = module.vllm_server.instance_id
}

output "base_url" {
  description = "OpenAI-compatible base URL of the inference server."
  value       = module.vllm_server.base_url
}
