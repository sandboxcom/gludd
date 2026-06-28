# Outputs for the vllm-server module.
# A stack composes this module and forwards output.user_data to its
# provider-specific compute resource (aws_instance.user_data etc.).

output "instance_id" {
  description = "Opaque identifier for the materialized server config. Stamps per-apply so dependent resources see changes."
  value       = terraform_data.vllm_server_cloud_init.id
}

output "base_url" {
  description = "OpenAI-compatible base URL the running server will expose once the stack's compute resource is up."
  value       = "http://localhost:8000/v1"
}

output "user_data" {
  description = "Cloud-init script that pulls the image and runs `vllm serve`. Stack forwards this to its provider-specific compute resource."
  value       = terraform_data.vllm_server_cloud_init.output.user_data
}

output "serve_command" {
  description = "The `docker run ... vllm serve` command embedded in user_data. Surfaced for logging/diff in plans."
  value       = terraform_data.vllm_server_cloud_init.output.serve_command
}
