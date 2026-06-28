output "instance_ip" {
  description = "Primary IP of the vLLM inference VM."
  value       = module.vllm_server.instance_ip
}

output "endpoint_url" {
  description = "OpenAI-compatible v1 endpoint."
  value       = module.vllm_server.endpoint_url
}
