output "instance_id" {
  description = "Opaque identifier for the QEMU VM configuration."
  value       = terraform_data.qemu_vm_config.id
}

output "cloud_init_config" {
  description = "Full cloud-init user-data YAML for the VM."
  value       = terraform_data.qemu_vm_config.output.cloud_init_full
}

output "ssh_command" {
  description = "SSH command to connect to the VM. Hostname is placeholder; resolve after deployment."
  value       = "ssh gludd@<vm-ip>"
}

output "inference_url" {
  description = "OpenAI-compatible inference endpoint URL once the VM is running."
  value       = "http://localhost:${var.host_port}/v1"
}
