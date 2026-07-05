output "instance_id" {
  description = "EC2 instance id of the deployed inference server."
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
