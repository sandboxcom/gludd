# Outputs for the onboard-iam-azure module.

output "principal_id" {
  description = "Object/principal id of the gludd operator managed identity (used for role assignments)."
  value       = azurerm_user_assigned_identity.gludd_operator.principal_id
}

output "client_id" {
  description = "Client (app) id of the gludd operator managed identity."
  value       = azurerm_user_assigned_identity.gludd_operator.client_id
}

output "tenant_id" {
  description = "Tenant id of the gludd operator managed identity."
  value       = azurerm_user_assigned_identity.gludd_operator.tenant_id
}

output "identity_id" {
  description = "Full ARM resource id of the gludd operator managed identity."
  value       = azurerm_user_assigned_identity.gludd_operator.id
}
