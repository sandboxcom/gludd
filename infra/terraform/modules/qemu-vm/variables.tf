variable "name" {
  description = "QEMU VM display name. Used for libvirt domain and volume names."
  type        = string
}

variable "vcpus" {
  description = "Number of vCPUs assigned to the VM."
  type        = number
  default     = 4
}

variable "memory_mb" {
  description = "RAM assigned to the VM in megabytes."
  type        = number
  default     = 16384
}

variable "disk_size_gb" {
  description = "Root disk size in gigabytes."
  type        = number
  default     = 100
}

variable "base_image_url" {
  description = "URL of the base OS qcow2 cloud image (e.g. Ubuntu cloud image)."
  type        = string
  default     = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

variable "storage_pool" {
  description = "Libvirt storage pool name where volumes are created."
  type        = string
  default     = "default"
}

variable "network_name" {
  description = "Libvirt network name to attach the VM to."
  type        = string
  default     = "default"
}

variable "host_port" {
  description = "Host port to forward to the VM's inference port (8000)."
  type        = number
  default     = 8000
}

variable "ssh_public_key" {
  description = "SSH public key injected into the VM via cloud-init."
  type        = string
}

variable "cloud_init_user_data" {
  description = "Engine-specific cloud-init script appended to the base setup. Provided by the inference server module (e.g. vllm-server.user_data)."
  type        = string
}

variable "extra_qemu_args" {
  description = "Extra arguments appended to the qemu-system-x86_64 invocation."
  type        = string
  default     = ""
}
