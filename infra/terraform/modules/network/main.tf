# Per-provider network module.
#
# Implements TERRAFORM_INFRA_STRUCTURE.md §5: opens the minimum required ports
# for a vLLM inference server (var.vllm_port from the operator-configurable
# CIDR, plus 22 for SSH from var.ssh_cidr) across the supported providers.
#
# Resources are gated by `count = var.provider == "<x>" ? 1 : 0` so a single
# stack only materializes the resource set for its cloud. runpod and vast are
# documented as N/A — both manage networking internally and expose the
# inference port by default.

terraform {
  required_version = ">= 1.4"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 4.0"
    }
    google = {
      source  = "hashicorp/google"
      version = ">= 4.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0"
    }
  }
}

locals {
  name = coalesce(var.name_prefix, "vllm")
}

# ---------------------------------------------------------------------------
# AWS — security group with vllm_port + ssh ingress, all egress.
# ---------------------------------------------------------------------------

resource "aws_security_group" "this" {
  count       = var.cloud == "aws" ? 1 : 0
  name        = "${local.name}-sg"
  description = "Inference server SG for ${local.name} (module network)"

  ingress {
    description = "vLLM inference port"
    from_port   = var.vllm_port
    to_port     = var.vllm_port
    protocol    = "tcp"
    cidr_blocks = [var.vllm_allowed_cidr]
  }

  ingress {
    description = "SSH from operator CIDR"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  egress {
    description = "all egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# GCP — compute firewall rules.
# ---------------------------------------------------------------------------

resource "google_compute_firewall" "vllm" {
  count   = var.cloud == "gcp" ? 1 : 0
  name    = "${local.name}-allow-vllm"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = [tostring(var.vllm_port)]
  }
  source_ranges = [var.vllm_allowed_cidr]
  target_tags   = [local.name]
}

resource "google_compute_firewall" "ssh" {
  count   = var.cloud == "gcp" ? 1 : 0
  name    = "${local.name}-allow-ssh"
  network = "default"
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = [var.ssh_cidr]
  target_tags   = [local.name]
}

# ---------------------------------------------------------------------------
# Azure — NSG + rules.
# ---------------------------------------------------------------------------

resource "azurerm_network_security_group" "this" {
  count               = var.cloud == "azure" ? 1 : 0
  name                = "${local.name}-nsg"
  location            = var.region
  resource_group_name = var.azure_resource_group
}

resource "azurerm_network_security_rule" "vllm" {
  count                       = var.cloud == "azure" ? 1 : 0
  name                        = "${local.name}-vllm"
  priority                    = 1000
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = tostring(var.vllm_port)
  source_address_prefix       = var.vllm_allowed_cidr
  destination_address_prefix  = "*"
  resource_group_name         = var.azure_resource_group
  network_security_group_name = azurerm_network_security_group.this[0].name
}

resource "azurerm_network_security_rule" "ssh" {
  count                       = var.cloud == "azure" ? 1 : 0
  name                        = "${local.name}-ssh"
  priority                    = 1010
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = var.ssh_cidr
  destination_address_prefix  = "*"
  resource_group_name         = var.azure_resource_group
  network_security_group_name = azurerm_network_security_group.this[0].name
}

# ---------------------------------------------------------------------------
# vSphere — no native firewall primitive; vSphere firewalling lives at the
# dvSwitch/distributed-port-group level. When var.vsphere_network is set the
# null_resource invokes govc to apply the operator's pre-defined port group.
# ---------------------------------------------------------------------------

resource "null_resource" "vsphere_port_group" {
  count = var.cloud == "vsphere" && var.vsphere_network != "" ? 1 : 0

  triggers = {
    network     = var.vsphere_network
    name_prefix = local.name
    vllm_port   = tostring(var.vllm_port)
  }

  provisioner "local-exec" {
    command = <<-EOT
      # Requires GOVC_URL, GOVC_USERNAME, GOVC_PASSWORD, GOVC_INSECURE in env.
      # Documents the intended port-group binding; concrete enforcement is at
      # the dvSwitch layer which govc only reflects, not enforces.
      if command -v govc >/dev/null 2>&1; then
        govc dvs.portgroup.info -dvs "${var.vsphere_network}" || \
          echo "network[vsphere]: ${var.vsphere_network} not found; skipping govc apply"
      else
        echo "network[vsphere]: govc not installed; recording port-group intent only"
      fi
    EOT
  }
}

# ---------------------------------------------------------------------------
# runpod / vast — N/A. Both providers manage networking internally and expose
# var.vllm_port on the public instance endpoint by default. No resources.
# ---------------------------------------------------------------------------
