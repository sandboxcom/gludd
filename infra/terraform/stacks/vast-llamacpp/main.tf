terraform {
  required_version = ">= 1.4"
}

# Vast.ai has no official Terraform provider in the registry. The stack composes
# the provider-agnostic llamacpp-server module; Vast.ai instance lifecycle is driven
# via the Vast.ai SDK at apply time outside Terraform. Add a provider block here
# only if/when a community vast-ai provider is published.

module "vllm_server" {
  source = "../../modules/llamacpp-server"

  image           = var.image
  gpus            = var.gpus
  model           = var.model
  region          = var.region
  instance_type   = var.instance_type
  extra_args      = var.extra_args
  max_cost_usd    = var.max_cost_usd
  timeout_minutes = var.timeout_minutes
}
