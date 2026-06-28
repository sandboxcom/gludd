# Outputs for the onboard-iam-gcp module.

output "service_account_email" {
  description = "Email of the gludd operator service account."
  value       = google_service_account.gludd_operator.email
}

output "service_account_id" {
  description = "Stable id of the gludd operator service account."
  value       = google_service_account.gludd_operator.account_id
}

output "service_account_key" {
  description = "Service-account JSON key (only populated when create_key = true). SENSITIVE — treat as a secret."
  value       = length(google_service_account_key.gludd_operator_key) > 0 ? google_service_account_key.gludd_operator_key[0].private_key : null
  sensitive   = true
}
