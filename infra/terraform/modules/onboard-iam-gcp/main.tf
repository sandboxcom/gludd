# GCP onboarding IAM module — provisions the least-privilege service account
# gludd uses to launch and tear down ephemeral GPU compute.
#
# Role set is the minimal set that
# src/general_ludd/infra/terraform.py::_generate_gcp requires to materialise
# its plan (google_compute_instance + google_compute_firewall). Asserted by
# tests/unit/test_onboard_gcp.py::TestTerraformModuleLeastPriv.

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {}

# The gludd operator service account.
# Default account_id: gludd-compute-operator (overridable via var.service_account_name).
resource "google_service_account" "gludd_operator" {
  account_id   = var.service_account_name
  display_name = var.display_name
  project      = var.project_id
}

# Custom least-privilege role. This custom role grants
# ONLY the instance/disk/address/network/machine-type permissions that
# gludd's Terraform graph emits (google_compute_instance +
# google_compute_disk + google_compute_address in _generate_gcp) and
# only the instance/disk/address/network/machine-type permissions needed here.
resource "google_project_iam_custom_role" "compute_operator" {
  role_id     = "gluddComputeOperator"
  title       = "Gludd Compute Operator (no setMetadata)"
  description = "Custom compute operator role with only the permissions needed by gludd."
  project     = var.project_id
  permissions = [
    "compute.acceleratorTypes.get",
    "compute.acceleratorTypes.list",
    "compute.addresses.create",
    "compute.addresses.delete",
    "compute.addresses.get",
    "compute.addresses.list",
    "compute.addresses.use",
    "compute.diskTypes.get",
    "compute.diskTypes.list",
    "compute.disks.create",
    "compute.disks.delete",
    "compute.disks.get",
    "compute.disks.list",
    "compute.disks.use",
    "compute.globalOperations.get",
    "compute.images.get",
    "compute.images.list",
    "compute.images.useReadOnly",
    "compute.instances.delete",
    "compute.instances.get",
    "compute.instances.insert",
    "compute.instances.list",
    "compute.instances.reset",
    "compute.instances.setDeletionProtection",
    "compute.instances.setLabels",
    "compute.instances.setMachineType",
    "compute.instances.setScheduling",
    "compute.instances.setServiceAccount",
    "compute.instances.setTags",
    "compute.instances.start",
    "compute.instances.stop",
    "compute.instances.update",
    "compute.machineTypes.get",
    "compute.machineTypes.list",
    "compute.networks.get",
    "compute.networks.list",
    "compute.projects.get",
    "compute.subnetworks.get",
    "compute.subnetworks.list",
    "compute.subnetworks.use",
    "compute.zoneOperations.get",
    "compute.zones.get",
    "compute.zones.list",
  ]
}

resource "google_project_iam_member" "compute_operator" {
  project = var.project_id
  role    = "projects/${var.project_id}/roles/${google_project_iam_custom_role.compute_operator.role_id}"
  member  = "serviceAccount:${google_service_account.gludd_operator.email}"
}

# Create + manage firewall rules (google_compute_firewall in _generate_gcp).
resource "google_project_iam_member" "compute_security_admin" {
  project = var.project_id
  role    = "roles/compute.securityAdmin"
  member  = "serviceAccount:${google_service_account.gludd_operator.email}"
}

# Allow gludd to attach the operator SA itself to the instances it creates.
resource "google_project_iam_member" "service_account_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.gludd_operator.email}"
}

# Emit runtime logs.
resource "google_project_iam_member" "logging_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.gludd_operator.email}"
}

# Optional: create a JSON key for local/CI auth. Disabled by default; enable
# with create_key = true only when ADC is not available.
resource "google_service_account_key" "gludd_operator_key" {
  count              = var.create_key ? 1 : 0
  service_account_id = google_service_account.gludd_operator.name
  public_key_type    = "TYPE_X509_PEM_FILE"
  private_key_type   = "TYPE_GOOGLE_CREDENTIALS_FILE"
}
