# ---------------------------------------------------------------------------
# Canonical third-party provider version contract for all gludd Terraform stacks.
# ---------------------------------------------------------------------------
# This file is the SINGLE SOURCE OF TRUTH for provider versions. Each stack
# under stacks/ pins its own required_providers to MATCH the versions declared
# here (Terraform resolves required_providers per-module; there is no cross-
# module include). Drift is blocked by `make tf-versions-check`, which runs
# scripts/check_tf_provider_versions.py.
#
# Why this matters: the shared plugin cache (TF_PLUGIN_CACHE_DIR, wired in the
# Makefile tf-* targets) downloads each provider binary ONCE into
# infra/terraform/.plugin-cache/ and every stack reuses it. Without a single
# version contract the cache would hold multiple copies and stacks would silently
# diverge. Running `terraform init` here (make tf-cache-warm) populates the
# cache with every provider below in a single pass.
#
# Design ref: docs/design/TERRAFORM_INFRA_STRUCTURE.md §10 #3 (resolved).
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.4"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.55"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.31"
    }
    vsphere = {
      source  = "vmware/vsphere"
      version = "~> 2.8"
    }
    runpod = {
      source  = "runpod/runpod"
      version = "~> 1.0"
    }
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.7"
    }
  }
}
