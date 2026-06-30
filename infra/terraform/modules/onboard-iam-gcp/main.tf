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

# Create + manage compute instances (google_compute_instance).
# This is the scoped instanceAdmin.v1 — NOT the broader compute.admin.
resource "google_project_iam_member" "compute_instance_admin" {
  project = var.project_id
  role    = "roles/compute.instanceAdmin.v1"
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
