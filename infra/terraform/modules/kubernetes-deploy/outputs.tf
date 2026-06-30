output "deployment_name" {
  description = "Name of the Kubernetes Deployment."
  value       = kubernetes_deployment_v1.inference_server.metadata[0].name
}

output "service_name" {
  description = "Name of the Kubernetes Service."
  value       = kubernetes_service_v1.inference_server.metadata[0].name
}

output "service_endpoint" {
  description = "Internal DNS endpoint of the inference Service."
  value       = "${kubernetes_service_v1.inference_server.metadata[0].name}.${kubernetes_service_v1.inference_server.metadata[0].namespace}.svc.cluster.local:${kubernetes_service_v1.inference_server.spec[0].port[0].port}"
}

output "namespace" {
  description = "Kubernetes namespace the resources are deployed into."
  value       = kubernetes_deployment_v1.inference_server.metadata[0].namespace
}

output "configmap_name" {
  description = "Name of the ConfigMap holding server configuration."
  value       = kubernetes_config_map_v1.server_config.metadata[0].name
}
