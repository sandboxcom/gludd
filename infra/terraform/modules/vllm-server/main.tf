# vLLM inference server module.
#
# Extracted from the string-interpolated HCL in
# src/general_ludd/infra/terraform.py::_engine_serve_cmd (engine == VLLM) and
# _user_data_script. The cloud-init body below is the static, reviewable form
# of what the runtime used to emit per provider; a stack composes this module
# and supplies provider-specific compute (aws_instance, google_compute_instance,
# azurerm_linux_virtual_machine, ...) referencing var.user_data.
#
# This module deliberately stays provider-agnostic: it owns the vLLM launch
# script + cost watchdog and exports them as outputs. The provider-specific
# resource lives in the stack so the engine body is defined once and reviewed
# in one place (Option B, TERRAFORM_INFRA_STRUCTURE.md §4).

locals {
  # Mirror of _engine_serve_cmd for InferenceEngine.VLLM in terraform.py.
  # shlex-equivalent quoting is handled by the stack passing already-safe values
  # via tfvars (validated by ComputeConfig field validators before reaching HCL).
  serve_command = join(" ", compact([
    "docker", "run",
    "--gpus", "all",
    "-p", "8000:8000",
    var.image,
    "--model", var.model,
    "--host", "0.0.0.0",
    "--port", "8000",
    var.enable_structured_outputs && var.guided_decoding_backend != "" ? "--guided-decoding-backend" : "",
    var.enable_structured_outputs && var.guided_decoding_backend != "" ? var.guided_decoding_backend : "",
    var.extra_args,
  ]))

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail

    # Pull and run vLLM inference server
    ${local.serve_command} &

    # Cost/TTL watchdog (see modules/gpu-cost-watchdog)
    echo "MAX_COST=${var.max_cost_usd}"    >> /etc/environment
    echo "TIMEOUT_MIN=${var.timeout_minutes}" >> /etc/environment
  EOT
}

# Materialized cloud-init so the module is structurally valid standalone and
# so the stack can read module.vllm-server.user_data. Idempotent: the value is
# derived purely from input variables. terraform_data is the built-in
# no-provider resource (Terraform >= 1.4).
resource "terraform_data" "vllm_server_cloud_init" {
  input = {
    image                     = var.image
    model                     = var.model
    gpus                      = var.gpus
    extra_args                = var.extra_args
    region                    = var.region
    instance_type             = var.instance_type
    max_cost_usd              = var.max_cost_usd
    timeout_minutes           = var.timeout_minutes
    guided_decoding_backend   = var.guided_decoding_backend
    enable_structured_outputs = var.enable_structured_outputs
    serve_command             = local.serve_command
    user_data                 = local.user_data
  }
}
