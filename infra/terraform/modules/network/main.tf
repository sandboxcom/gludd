# Per-provider network module (Phase 1 stub).
#
# TERRAFORM_INFRA_STRUCTURE.md §5: network holds per-provider NSG/firewall
# rules. Per-provider network primitives differ enough (aws_security_group vs
# google_compute_firewall vs azurerm_network_security_group vs vsphere_port_group)
# that the concrete resource lives in each stack; this module exists to:
#   (a) document the shared ingress contract (port 8000 from var.allowed_cidr),
#   (b) give stacks a stable place to forward the rule into provider-specific
#       primitives during Phase 2/4 composition.
#
# Phase 1 leaves it as a no-op stub with the contract documented; stacks still
# inline their own security group for now (the legacy string-interpolated path
# in terraform.py is untouched per Phase 4 scope).

# No-op placeholder so the module is structurally valid and self-validatable
# without a cloud provider plugin. Phase 2 replaces this with real per-provider
# resources gated on a `provider_kind` variable.
resource "terraform_data" "network_contract" {
  input = {
    allowed_cidr   = var.allowed_cidr
    inference_port = var.inference_port
    note           = "Phase 1 stub: concrete NSG/firewall lives in each provider stack."
  }
}
