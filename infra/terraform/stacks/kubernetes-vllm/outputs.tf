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

output "watchdog_user_data" {
  description = "Cloud-init fragment from the gpu-cost-watchdog module."
  value       = module.gpu_cost_watchdog.user_data
}
