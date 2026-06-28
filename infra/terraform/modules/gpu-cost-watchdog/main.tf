# GPU cost / TTL watchdog module.
#
# Extracted from the watchdog half of
# src/general_ludd/infra/terraform.py::_user_data_script, which writes
# MAX_COST / TIMEOUT_MIN into /etc/environment. This module materializes the
# full shutdown script that consumes those env vars, so the watchdog logic is
# reviewed in one place rather than re-rolled per provider.
#
# A stack forwards output.script to its provider-specific compute resource
# (typically chained after the engine module's user_data via cloud-init
# write_files/merge logic — kept out of scope for Phase 1).

locals {
  # Polls wall-clock uptime and self-terminates when TIMEOUT_MIN is hit.
  # Cost check is provider-specific (AWS Cost Explorer / Azure metering) and
  # is left as a stack override hook for Phase 2. Designed to run under a
  # systemd Type=oneshot Restart=always unit installed by the stack cloud-init.
  watchdog_script = <<-EOT
    #!/bin/bash
    set -uo pipefail
    # MAX_COST and TIMEOUT_MIN are sourced from /etc/environment by the engine
    # module's cloud-init. Missing values => watchdog no-ops.
    : "$${MAX_COST:=0}"
    : "$${TIMEOUT_MIN:=0}"
    [ "$$TIMEOUT_MIN" -gt 0 ] || exit 0

    START=$$(date +%s)
    while true; do
      sleep 60
      UPTIME_MIN=$$(( ($$(date +%s) - START) / 60 ))
      if [ "$$UPTIME_MIN" -ge "$$TIMEOUT_MIN" ]; then
        echo "watchdog: TIMEOUT_MIN=$$TIMEOUT_MIN reached ($$UPTIME_MIN min) - shutting down"
        sudo shutdown -h now
        exit 0
      fi
    done
  EOT
}

resource "terraform_data" "gpu_cost_watchdog" {
  input = {
    max_cost_usd    = var.max_cost_usd
    timeout_minutes = var.timeout_minutes
    script          = local.watchdog_script
  }
}
