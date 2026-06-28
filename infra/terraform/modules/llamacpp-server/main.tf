# llama.cpp inference server module.
#
# Extracted from src/general_ludd/infra/terraform.py::_engine_serve_cmd
# (engine == LLAMACPP) and _user_data_script. Same shape as vllm-server but
# for the llama.cpp OpenAI-compatible server (`llama_cpp.server` container).
#
# Provider-agnostic: owns the launch script + watchdog; the stack supplies the
# provider-specific compute resource referencing output.user_data.

locals {
  # Mirror of _engine_serve_cmd for InferenceEngine.LLAMACPP.
  # Note llama.cpp uses `-m` (not `--model`) for the model path.
  serve_command = join(" ", [
    "docker", "run",
    "--gpus", "all",
    "-p", "8000:8000",
    var.image,
    "-m", var.model,
    "--host", "0.0.0.0",
    "--port", "8000",
    var.extra_args,
  ])

  user_data_template = <<-EOT
    #!/bin/bash
    set -euxo pipefail

    # Pull and run llama.cpp server
    ${local.serve_command} &

    # Cost/TTL watchdog (see modules/gpu-cost-watchdog)
    echo "MAX_COST=${var.max_cost_usd}"       >> /etc/environment
    echo "TIMEOUT_MIN=${var.timeout_minutes}" >> /etc/environment
  EOT
}

resource "terraform_data" "llamacpp_server_cloud_init" {
  input = {
    image           = var.image
    model           = var.model
    gpus            = var.gpus
    extra_args      = var.extra_args
    region          = var.region
    instance_type   = var.instance_type
    max_cost_usd    = var.max_cost_usd
    timeout_minutes = var.timeout_minutes
    serve_command   = local.serve_command
    user_data       = local.user_data_template
  }
}
