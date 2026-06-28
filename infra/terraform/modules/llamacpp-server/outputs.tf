# Outputs for the llamacpp-server module. Same shape as vllm-server.

output "instance_id" {
  description = "Opaque identifier for the materialized server config."
  value       = terraform_data.llamacpp_server_cloud_init.id
}

output "base_url" {
  description = "OpenAI-compatible base URL the running llama.cpp server will expose once the stack's compute resource is up."
  value       = "http://localhost:8000/v1"
}

output "user_data" {
  description = "Cloud-init script that pulls the image and runs the llama.cpp server. Stack forwards this to its provider-specific compute resource."
  value       = terraform_data.llamacpp_server_cloud_init.output.user_data
}

output "serve_command" {
  description = "The `docker run ... llama_cpp.server` command embedded in user_data. Surfaced for logging/diff in plans."
  value       = terraform_data.llamacpp_server_cloud_init.output.serve_command
}
