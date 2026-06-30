variable "image" {
  description = "Container image for the inference engine."
  type        = string
}

variable "model_name" {
  description = "Model identifier served by the inference engine. Must match the ComputeConfig.model_name allowlist."
  type        = string
}

variable "engine" {
  description = "Inference engine to run (vllm or llamacpp)."
  type        = string
  default     = "vllm"

  validation {
    condition     = contains(["vllm", "llamacpp"], var.engine)
    error_message = "engine must be 'vllm' or 'llamacpp'."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for the deployment."
  type        = string
  default     = "default"
}

variable "replicas" {
  description = "Number of deployment replicas."
  type        = number
  default     = 1
}

variable "gpu_count" {
  description = "Number of GPUs to request per pod."
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
  description = "GPU vendor for the resource request (nvidia.com/gpu or amd.com/gpu)."
  type        = string
  default     = "nvidia.com/gpu"
}

variable "model_pvc_name" {
  description = "Name of an existing PVC containing model files. Leave empty to skip model volume mount."
  type        = string
  default     = ""
}

variable "model_mount_path" {
  description = "Container path where the model PVC is mounted."
  type        = string
  default     = "/models"
}

variable "extra_args" {
  description = "Extra arguments appended to the inference server invocation."
  type        = string
  default     = ""
}

variable "service_type" {
  description = "Kubernetes Service type."
  type        = string
  default     = "ClusterIP"
}

variable "service_port" {
  description = "Port exposed by the inference server inside the container."
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = "HTTP path for liveness and readiness probes."
  type        = string
  default     = "/health"
}

variable "liveness_initial_delay" {
  description = "Seconds before the first liveness probe fires."
  type        = number
  default     = 60
}

variable "readiness_initial_delay" {
  description = "Seconds before the first readiness probe fires."
  type        = number
  default     = 30
}

variable "labels" {
  description = "Additional labels to apply to all resources."
  type        = map(string)
  default     = {}
}
