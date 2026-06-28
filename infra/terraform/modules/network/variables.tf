# Input variables for the network module (Phase 1 stub).

variable "allowed_cidr" {
  description = "CIDR allowed to reach the inference server on the inference port. Mirrors ComputeConfig.allowed_cidr."
  type        = string
  default     = "0.0.0.0/0"
}

variable "inference_port" {
  description = "Port the inference server listens on."
  type        = number
  default     = 8000
}
