# Inputs for the gludd onboarding IAM module.
# See tests/unit/test_onboard_aws.py for the static pin on policy shape.

variable "role_name" {
  description = "Name of the IAM role gludd will assume / attach to EC2 instances. Must be unique within the account."
  type        = string
  default     = "gludd-compute-operator"
}

variable "policy_name" {
  description = "Name of the least-privilege IAM policy attached to the operator role."
  type        = string
  default     = "gludd-compute-least-priv"
}

variable "region" {
  description = "AWS region gludd will deploy compute into. Used to scope EC2/EBS/EIP/log-group ARNs in the policy. Cannot be changed after apply without a role recreation."
  type        = string
  default     = "us-east-1"
}

variable "tags" {
  description = "Tags applied to the IAM role, policy, and instance profile."
  type        = map(string)
  default = {
    ManagedBy = "gludd"
    Component = "onboard-iam"
    Purpose   = "least-privilege-compute-operator"
  }
}
