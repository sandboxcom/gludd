variable "deployment_name" {
  description = "Unique lowercase Gludd run name used as the owned-resource prefix."
  type        = string

  validation {
    condition     = can(regex("^gludd-[a-z0-9][a-z0-9-]{2,34}$", var.deployment_name))
    error_message = "deployment_name must be a 9-40 character gludd-prefixed lowercase label."
  }
}

variable "resource_group_id" {
  description = "Canonical existing resource-group ID; this module never creates or deletes it."
  type        = string

  validation {
    condition = can(regex(
      "^/subscriptions/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/resourceGroups/[A-Za-z0-9._()-]{1,90}$",
      var.resource_group_id,
    ))
    error_message = "resource_group_id must be one canonical existing Azure resource-group ID."
  }
}

variable "resource_group_name" {
  description = "Name of the existing exact-scope resource group delegated to this worker lease."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must be one valid Azure resource-group name."
  }
}

variable "location" {
  description = "Azure region proven available by the read-only inventory identity."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{2,32}$", var.location))
    error_message = "location must be one canonical lowercase Azure region."
  }
}

variable "vm_size" {
  description = "Exact ARM SKU selected from live price, quota, capacity, and topology evidence."
  type        = string

  validation {
    condition     = startswith(var.vm_size, "Standard_") && can(regex("^Standard_[A-Za-z0-9_]+$", var.vm_size))
    error_message = "vm_size must be one canonical Standard_* ARM SKU returned by inventory."
  }
}

variable "instance_count" {
  description = "Replica count proven necessary by measured work demand; two is the ZDD minimum."
  type        = number

  validation {
    condition     = var.instance_count >= 2 && var.instance_count <= 100 && floor(var.instance_count) == var.instance_count
    error_message = "instance_count must be an integer in 2..100."
  }
}

variable "availability_zones" {
  description = "Live-attested zones; RDMA layouts may use at most one zone."
  type        = list(string)
  default     = []

  validation {
    condition = (
      length(var.availability_zones) <= 3 &&
      length(distinct(var.availability_zones)) == length(var.availability_zones) &&
      alltrue([for zone in var.availability_zones : can(regex("^[1-3]$", zone))])
    )
    error_message = "availability_zones must contain distinct Azure zones 1, 2, or 3."
  }
}

variable "platform_fault_domain_count" {
  description = "Fault-domain count supported by current regional VMSS evidence."
  type        = number
  default     = 1

  validation {
    condition     = var.platform_fault_domain_count >= 1 && var.platform_fault_domain_count <= 5 && floor(var.platform_fault_domain_count) == var.platform_fault_domain_count
    error_message = "platform_fault_domain_count must be an integer in 1..5."
  }
}

variable "single_placement_group" {
  description = "Non-RDMA placement choice; RDMA always forces one placement group."
  type        = bool
  default     = false
}

variable "rdma_enabled" {
  description = "Whether inventory and guest attestation require an InfiniBand/RDMA layout."
  type        = bool
  default     = false
}

variable "accelerated_networking_enabled" {
  description = "Whether live SKU capabilities prove accelerated networking support."
  type        = bool
}

variable "user_assigned_identity_id" {
  description = "Existing user-assigned identity used by guests; the module grants it no roles."
  type        = string

  validation {
    condition = can(regex(
      "^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9._()-]{1,90}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/[A-Za-z0-9._()-]{1,128}$",
      var.user_assigned_identity_id,
    ))
    error_message = "user_assigned_identity_id must be one canonical Azure identity resource ID."
  }
}

variable "admin_username" {
  description = "Unprivileged SSH account configured for the later Ansible bootstrap phase."
  type        = string
  default     = "gludd"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]{2,31}$", var.admin_username))
    error_message = "admin_username must be one bounded lowercase Linux account name."
  }
}

variable "ssh_public_key" {
  description = "Public SSH key only; private authentication material never enters OpenTofu state."
  type        = string

  validation {
    condition = can(regex(
      "^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+( .*)?$",
      trimspace(var.ssh_public_key),
    )) && length(var.ssh_public_key) <= 16384
    error_message = "ssh_public_key must be one bounded OpenSSH public key."
  }
}

variable "controller_cidr" {
  description = "Single privately routed controller IPv4 address allowed to reach the workers."
  type        = string

  validation {
    condition = (
      can(cidrhost(var.controller_cidr, 0)) &&
      endswith(var.controller_cidr, "/32") &&
      length(regexall(":", var.controller_cidr)) == 0
    )
    error_message = "controller_cidr must be exactly one IPv4 /32."
  }
}

variable "inference_port" {
  description = "TCP inference port later configured by the Ansible model-server role."
  type        = number
  default     = 8000

  validation {
    condition     = var.inference_port >= 1024 && var.inference_port <= 65535 && var.inference_port != 22
    error_message = "inference_port must be an unprivileged TCP port other than SSH."
  }
}

variable "health_port" {
  description = "Local port exposed by the attested runner health endpoint."
  type        = number
  default     = 8000

  validation {
    condition     = var.health_port >= 1024 && var.health_port <= 65535
    error_message = "health_port must be an unprivileged TCP port."
  }
}

variable "health_path" {
  description = "Local rich-health endpoint installed before instances enter rotation."
  type        = string
  default     = "/healthz"

  validation {
    condition     = can(regex("^/[A-Za-z0-9/_-]{1,127}$", var.health_path))
    error_message = "health_path must be one bounded absolute path."
  }
}

variable "health_interval_seconds" {
  description = "Application Health rich-state probe interval."
  type        = number
  default     = 10

  validation {
    condition     = var.health_interval_seconds >= 5 && var.health_interval_seconds <= 60 && floor(var.health_interval_seconds) == var.health_interval_seconds
    error_message = "health_interval_seconds must be an integer in 5..60."
  }
}

variable "health_probe_count" {
  description = "Consecutive rich-health responses required for a state transition."
  type        = number
  default     = 3

  validation {
    condition     = var.health_probe_count >= 1 && var.health_probe_count <= 24 && floor(var.health_probe_count) == var.health_probe_count
    error_message = "health_probe_count must be an integer in 1..24."
  }
}

variable "health_grace_period_seconds" {
  description = "Initialization interval before rich health may become unknown."
  type        = number
  default     = 900

  validation {
    condition     = var.health_grace_period_seconds >= 60 && var.health_grace_period_seconds <= 14400 && floor(var.health_grace_period_seconds) == var.health_grace_period_seconds
    error_message = "health_grace_period_seconds must be an integer in 60..14400."
  }
}

variable "repair_grace_period" {
  description = "Delay before Azure replaces an instance that remains unhealthy."
  type        = string
  default     = "PT30M"

  validation {
    condition     = can(regex("^PT([1-8][0-9]|90)M$", var.repair_grace_period))
    error_message = "repair_grace_period must be an ISO-8601 duration from PT10M through PT90M."
  }
}

variable "max_batch_percent" {
  description = "Maximum rolling replacement batch; health gates every batch."
  type        = number
  default     = 20

  validation {
    condition     = var.max_batch_percent >= 1 && var.max_batch_percent <= 50 && floor(var.max_batch_percent) == var.max_batch_percent
    error_message = "max_batch_percent must be an integer in 1..50."
  }
}

variable "pause_between_batches" {
  description = "Observable stabilization pause between rolling batches."
  type        = string
  default     = "PT30S"

  validation {
    condition     = can(regex("^PT([1-9][0-9]?|[1-5][0-9]{2}|600)S$", var.pause_between_batches))
    error_message = "pause_between_batches must be PT1S through PT600S."
  }
}

variable "vnet_address_space" {
  description = "Private address space dedicated to this owned worker set."
  type        = string
  default     = "10.43.0.0/16"

  validation {
    condition     = can(cidrhost(var.vnet_address_space, 0))
    error_message = "vnet_address_space must be one valid CIDR block."
  }
}

variable "subnet_address_prefix" {
  description = "Private subnet inside vnet_address_space."
  type        = string
  default     = "10.43.1.0/24"

  validation {
    condition     = can(cidrhost(var.subnet_address_prefix, 0))
    error_message = "subnet_address_prefix must be one valid CIDR block."
  }
}

variable "egress_idle_timeout_minutes" {
  description = "Bounded NAT flow idle timeout for downloads and telemetry."
  type        = number
  default     = 10

  validation {
    condition     = var.egress_idle_timeout_minutes >= 4 && var.egress_idle_timeout_minutes <= 120 && floor(var.egress_idle_timeout_minutes) == var.egress_idle_timeout_minutes
    error_message = "egress_idle_timeout_minutes must be an integer in 4..120."
  }
}

variable "os_disk_storage_account_type" {
  description = "Managed OS-disk tier selected from the VM SKU's live capabilities."
  type        = string
  default     = "Premium_LRS"

  validation {
    condition     = can(regex("^(Standard_LRS|StandardSSD_LRS|Premium_LRS)$", var.os_disk_storage_account_type))
    error_message = "os_disk_storage_account_type must be a supported single-region managed-disk tier."
  }
}

variable "os_disk_size_gb" {
  description = "Bounded managed OS-disk capacity."
  type        = number
  default     = 128

  validation {
    condition     = var.os_disk_size_gb >= 64 && var.os_disk_size_gb <= 2048 && floor(var.os_disk_size_gb) == var.os_disk_size_gb
    error_message = "os_disk_size_gb must be an integer in 64..2048."
  }
}

variable "cache_disk_enabled" {
  description = "Attach an ephemeral model cache only after independent price attestation."
  type        = bool
  default     = false
}

variable "cache_price_attested" {
  description = "Whether current regional managed-disk price evidence covers the cache tier."
  type        = bool
  default     = false
}

variable "cache_disk_storage_account_type" {
  description = "Independently priced managed model-cache disk tier."
  type        = string
  default     = "Premium_LRS"

  validation {
    condition     = can(regex("^(StandardSSD_LRS|Premium_LRS)$", var.cache_disk_storage_account_type))
    error_message = "cache_disk_storage_account_type must be StandardSSD_LRS or Premium_LRS."
  }
}

variable "cache_disk_size_gb" {
  description = "Per-instance ephemeral model-cache disk capacity."
  type        = number
  default     = 256

  validation {
    condition     = var.cache_disk_size_gb >= 32 && var.cache_disk_size_gb <= 32767 && floor(var.cache_disk_size_gb) == var.cache_disk_size_gb
    error_message = "cache_disk_size_gb must be an integer in 32..32767."
  }
}

variable "image_publisher" {
  description = "Marketplace image publisher selected by policy."
  type        = string
  default     = "Canonical"
}

variable "image_offer" {
  description = "Marketplace image offer selected by policy."
  type        = string
  default     = "0001-com-ubuntu-server-jammy"
}

variable "image_sku" {
  description = "Marketplace image SKU selected by policy."
  type        = string
  default     = "22_04-lts-gen2"
}

variable "image_version" {
  description = "Immutable marketplace image version resolved before provisioning."
  type        = string

  validation {
    condition = (
      lower(var.image_version) != "latest" &&
      can(regex("^[0-9]+(\\.[0-9]+){2,3}$", var.image_version))
    )
    error_message = "image_version must be an immutable numeric marketplace version, never latest."
  }
}

variable "use_spot" {
  description = "Create evictable Spot replicas only when the planner accepts interruption risk."
  type        = bool
  default     = false
}

variable "max_spot_price" {
  description = "Azure Spot hourly bid, or -1 for the current pay-as-you-go cap."
  type        = number
  default     = -1

  validation {
    condition     = var.max_spot_price == -1 || var.max_spot_price > 0
    error_message = "max_spot_price must be -1 or a positive hourly USD bid."
  }
}

variable "owner_token" {
  description = "Bounded lease owner used to prove cleanup authority."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{7,63}$", var.owner_token))
    error_message = "owner_token must be one 8-64 character lowercase lease label."
  }
}

variable "trace_id" {
  description = "Secret-free correlation ID emitted across provision, bootstrap, work, and teardown."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.trace_id))
    error_message = "trace_id must be 32 lowercase hexadecimal characters."
  }
}

variable "expires_at_utc" {
  description = "Immutable whole-second UTC teardown deadline enforced by the lifecycle supervisor."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.expires_at_utc))
    error_message = "expires_at_utc must be a whole-second UTC RFC3339 timestamp."
  }
}

variable "tags" {
  description = "Additional non-secret tags; Gludd ownership tags always take precedence."
  type        = map(string)
  default     = {}
}
