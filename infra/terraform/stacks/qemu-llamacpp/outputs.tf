output "instance_id" {
  description = "Provider-assigned instance ID of the QEMU VM."
  value       = module.qemu_vm.instance_id
}

output "inference_url" {
  description = "OpenAI-compatible llama.cpp inference endpoint URL."
  value       = module.qemu_vm.inference_url
}

output "ssh_command" {
  description = "SSH command to access the VM."
  value       = module.qemu_vm.ssh_command
}

output "serve_command" {
  description = "The llama.cpp docker run command deployed to the VM."
  value       = module.llamacpp_server.serve_command
}
