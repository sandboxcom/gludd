output "deployment_name" {
  description = "Name of the deployed Kubernetes Deployment."
  value       = module.kubernetes_deploy.deployment_name
}

output "service_endpoint" {
  description = "Internal DNS endpoint of the inference Service."
  value       = module.kubernetes_deploy.service_endpoint
}

output "namespace" {
  description = "Kubernetes namespace deployed into."
  value       = module.kubernetes_deploy.namespace
}
