# Outputs for the network module.
#
# security_group_id / security_group_arn are provider-dependent: AWS exposes
# both directly; GCP exposes the firewall rule self-links; Azure exposes the
# NSG id; vsphere/runpod/vast expose empty strings (no native primitive).

output "security_group_id" {
  description = "Provider-native id of the network primitive created by the module (AWS SG id, GCP firewall self-link, Azure NSG id, or empty string for providers with no native primitive)."
  value = coalesce(
    try(aws_security_group.this[0].id, ""),
    try(google_compute_firewall.vllm[0].self_link, ""),
    try(azurerm_network_security_group.this[0].id, ""),
    try(null_resource.vsphere_port_group[0].id, ""),
  )
}

output "security_group_arn" {
  description = "Provider-native ARN/self-link of the network primitive, or empty string when the provider does not expose one."
  value = coalesce(
    try(aws_security_group.this[0].arn, ""),
    try(google_compute_firewall.vllm[0].self_link, ""),
    try(azurerm_network_security_group.this[0].id, ""),
    try(null_resource.vsphere_port_group[0].id, ""),
  )
}

output "vllm_port" {
  description = "Pass-through of var.vllm_port."
  value       = var.vllm_port
}

output "name_prefix" {
  description = "Resolved resource-name prefix used by the module."
  value       = coalesce(var.name_prefix, "vllm")
}
