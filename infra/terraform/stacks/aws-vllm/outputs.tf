output "instance_id" {
  description = "Provider-assigned instance id of the deployed inference server."
  value       = module.vllm_server.instance_id
}

output "instance_resource_id" {
  description = "EC2 instance resource id of the deployed compute instance."
  value       = aws_instance.inference.id
}

output "public_ip" {
  description = "Public IP address of the inference server."
  value       = aws_instance.inference.public_ip
}

output "base_url" {
  description = "OpenAI-compatible base URL of the inference server."
  value       = module.vllm_server.base_url
}
