# GPU cost / TTL watchdog module — real cloud-init self-termination.
#
# Implements TERRAFORM_INFRA_STRUCTURE.md §5: renders a cloud-init fragment
# that materializes /usr/local/bin/gpu-cost-watchdog.sh plus a systemd unit
# on first boot, then starts the unit. The script polls every
# var.poll_interval_seconds, queries the cloud API for the live hourly price
# of the running instance (when supported), accumulates spend, and calls the
# cloud terminate API once var.max_cost_usd or var.timeout_minutes is hit.
#
# Stacks compose this module's `user_data` output (a #cloud-config YAML
# fragment) alongside the vllm-server module's user_data via cloud-init
# multipart merge.

terraform {
  required_version = ">= 1.4"
}

locals {
  script_path = "/usr/local/bin/gpu-cost-watchdog.sh"

  # The shutdown script. Values are interpolated by Terraform at apply time;
  # $${VAR} escapes are preserved verbatim as shell-variable expansions in the
  # rendered script.
  watchdog_script = <<-EOT
    #!/bin/bash
    set -uo pipefail

    MAX_COST="${var.max_cost_usd}"
    TIMEOUT_MIN="${var.timeout_minutes}"
    POLL_INTERVAL="${var.poll_interval_seconds}"
    REGION="${var.region}"
    INSTANCE_ID="${var.instance_id}"
    CLOUD="${var.cloud}"

    # Fall back to the metadata service when Terraform did not supply an id.
    if [ -z "$${INSTANCE_ID}" ]; then
      case "$${CLOUD}" in
        aws)
          INSTANCE_ID=$$(curl -sf http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo "")
          ;;
        gcp)
          INSTANCE_ID=$$(curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name 2>/dev/null || echo "")
          ;;
        azure)
          INSTANCE_ID=$$(curl -sf -H "Metadata: true" "http://169.254.169.254/metadata/instance/compute/name?api-version=2021-02-01" 2>/dev/null || echo "")
          ;;
      esac
    fi

    # 0 disables either bound so the module is usable as TTL-only, cost-only,
    # or both.
    [ "$${MAX_COST}" -gt 0 ] 2>/dev/null || MAX_COST=0
    [ "$${TIMEOUT_MIN}" -gt 0 ] 2>/dev/null || TIMEOUT_MIN=0

    START=$$(date +%s)
    ACCUMULATED="0"

    terminate_instance() {
      echo "watchdog: terminating instance=$${INSTANCE_ID} cloud=$${CLOUD} region=$${REGION} accumulated=$${ACCUMULATED}"
      case "$${CLOUD}" in
        aws)
          aws ec2 terminate-instances --instance-ids "$${INSTANCE_ID}" --region "$${REGION}" 2>&1 || sudo shutdown -h now
          ;;
        gcp)
          gcloud compute instances delete "$${INSTANCE_ID}" --zone="$${REGION}" --quiet 2>&1 || sudo shutdown -h now
          ;;
        azure)
          az vm delete --ids "$${INSTANCE_ID}" --yes 2>&1 || sudo shutdown -h now
          ;;
        *)
          sudo shutdown -h now
          ;;
      esac
    }

    while true; do
      sleep "$${POLL_INTERVAL}"
      NOW=$$(date +%s)
      UPTIME_MIN=$$(( (NOW - START) / 60 ))

      HOURLY="0"
      case "$${CLOUD}" in
        aws)
          INST_TYPE=$$(aws ec2 describe-instances \
            --instance-ids "$${INSTANCE_ID}" \
            --region "$${REGION}" \
            --query 'Reservations[0].Instances[0].InstanceType' \
            --output text 2>/dev/null || echo "")
          if [ -n "$${INST_TYPE}" ] && [ "$${INST_TYPE}" != "None" ]; then
            SPOT=$$(aws ec2 describe-spot-price-history \
              --instance-types "$${INST_TYPE}" \
              --region "$${REGION}" \
              --query 'SpotPriceHistory[0].SpotPrice' \
              --output text 2>/dev/null || echo "")
            if [ -n "$${SPOT}" ] && [ "$${SPOT}" != "None" ]; then
              HOURLY="$${SPOT}"
            fi
          fi
          ;;
        gcp|azure|*)
          # Per-provider price APIs differ and require additional auth/labels
          # that the stack may not expose; default to 0 (TTL-only enforcement)
          # so the watchdog remains correct in all configurations. Stacks that
          # need cost accounting can override the rendered script via cloud-init
          # multipart merge.
          HOURLY="0"
          ;;
      esac

      ACCUMULATED=$$(awk -v a="$${ACCUMULATED}" -v h="$${HOURLY}" -v p="$${POLL_INTERVAL}" \
        'BEGIN { printf "%.6f", a + (h * p / 3600) }')

      if [ "$${MAX_COST}" -gt 0 ] && awk -v a="$${ACCUMULATED}" -v m="$${MAX_COST}" 'BEGIN { exit !(a >= m) }'; then
        echo "watchdog: accumulated=$${ACCUMULATED} USD >= MAX_COST=$${MAX_COST} USD"
        terminate_instance
        exit 0
      fi

      if [ "$${TIMEOUT_MIN}" -gt 0 ] && [ "$${UPTIME_MIN}" -ge "$${TIMEOUT_MIN}" ]; then
        echo "watchdog: uptime=$${UPTIME_MIN} min >= TIMEOUT_MIN=$${TIMEOUT_MIN} min"
        terminate_instance
        exit 0
      fi
    done
  EOT

  systemd_unit = <<-EOT
    [Unit]
    Description=GPU cost / TTL watchdog
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=oneshot
    ExecStart=${local.script_path}
    Restart=always
    RestartSec=10

    [Install]
    WantedBy=multi-user.target
  EOT

  # cloud-init document rendered via yamlencode so the embedded script body is
  # escaped correctly (no manual indentation / quoting required).
  cloud_init_map = {
    write_files = [
      {
        path        = local.script_path
        permissions = "0755"
        owner       = "root:root"
        content     = local.watchdog_script
      },
      {
        path        = "/etc/systemd/system/gpu-cost-watchdog.service"
        permissions = "0644"
        owner       = "root:root"
        content     = local.systemd_unit
      }
    ]
    runcmd = [
      ["systemctl", "daemon-reload"],
      ["systemctl", "enable", "--now", "gpu-cost-watchdog.service"]
    ]
  }

  cloud_init_yaml = "#cloud-config\n${yamlencode(local.cloud_init_map)}"
}

# terraform_data is the no-provider resource (Terraform >= 1.4). The module is
# therefore structurally validatable without any cloud provider plugin.
resource "terraform_data" "gpu_cost_watchdog" {
  input = {
    script_path     = local.script_path
    script          = local.watchdog_script
    cloud_init      = local.cloud_init_yaml
    max_cost_usd    = var.max_cost_usd
    timeout_minutes = var.timeout_minutes
  }
}
