variable "kubeconfig_path" {
  description = "Path to the kubeconfig file for the Kubernetes cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "kubeconfig_context" {
  description = "Kubeconfig context to use."
  type        = string
  default     = ""
}

variable "image" {
  description = "Container image for vLLM inference server."
  type        = string
  default     = "vllm/vllm-openai:latest"
}

variable "model" {
  description = "Model identifier served by vLLM."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace."
  type        = string
  default     = "default"
}

variable "replicas" {
  description = "Number of deployment replicas."
  type        = number
  default     = 1
}

variable "gpu_count" {
  description = "Number of GPUs per pod."
  type        = number
  default     = 1
}

variable "cpu_request" {
  description = "CPU cores requested per pod."
  type        = string
  default     = "4"
}

variable "memory_request" {
  description = "Memory requested per pod."
  type        = string
  default     = "8Gi"
}

variable "cpu_limit" {
  description = "CPU cores limit per pod."
  type        = string
  default     = "8"
}

variable "memory_limit" {
  description = "Memory limit per pod."
  type        = string
  default     = "16Gi"
}

variable "gpu_vendor" {
  description = "GPU vendor for the resource request."
  type        = string
  default     = "nvidia.com/gpu"
}

variable "model_pvc_name" {
  description = "Name of an existing PVC containing model files."
  type        = string
  default     = ""
}

variable "model_mount_path" {
  description = "Container path where the model PVC is mounted."
  type        = string
  default     = "/models"
}

variable "extra_args" {
  description = "Extra arguments for vLLM serve."
  type        = string
  default     = ""
}

variable "service_type" {
  description = "Kubernetes Service type."
  type        = string
  default     = "ClusterIP"
}

variable "service_port" {
  description = "Port the inference server listens on."
  type        = number
  default     = 8000
}
