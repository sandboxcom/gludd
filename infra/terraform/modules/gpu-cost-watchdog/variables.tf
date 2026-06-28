# Input variables for the gpu-cost-watchdog module.

variable "max_cost_usd" {
  description = "Cost ceiling (USD). The watchdog shuts the instance down once cumulative spend is estimated to reach this."
  type        = number
  default     = 10
}

variable "timeout_minutes" {
  description = "Wall-clock TTL (minutes). The watchdog shuts the instance down after this many minutes of uptime, regardless of cost."
  type        = number
  default     = 60
}
