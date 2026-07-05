output "instance_id" {
  description = "GCE instance id of the deployed inference server."
  value       = google_compute_instance.inference.instance_id
}

output "public_ip" {
  description = "Public IP address of the inference server."
  value       = google_compute_instance.inference.network_interface[0].access_config[0].nat_ip
}

output "base_url" {
  description = "OpenAI-compatible base URL of the inference server."
  value       = module.vllm_server.base_url
}
