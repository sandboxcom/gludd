# Gludd onboarding IAM module — least-privilege role + policy for
# `gludd onboard aws`.
#
# The role this module provisions is the security boundary for everything
# gludd does in your AWS account. It is consumed in two ways:
#   1. By Terraform (the credentials the user creates from this role) to run
#      `terraform apply` against the resources in
#      src/general_ludd/infra/terraform.py::_generate_aws (aws_instance,
#      aws_security_group, aws_ami data sources, root_block_device, tags).
#   2. Optionally as an instance profile attached to the resulting EC2 GPU
#      instances, so the cost/TTL watchdog and CloudWatch Logs emission work
#      without a second role.
#
# The policy grants ONLY the actions that gludd's Terraform graph emits and
# NOTHING ELSE. The policy document lives in policy.json (pure JSON, not HCL)
# so it is statically auditable and machine-checkable — see
# tests/unit/test_onboard_aws.py for the static pin on the exact action set.
# An explicit Deny block forbids IAM role/user creation and policy attachment
# so the operator cannot escalate itself or mint new principals.
#
# Least-privilege hardening (2026-07-25):
#   — IamPassRoleSelfOnly restricts to the operator's own role ARN only (not *)
#   — Condition on ec2:InstanceType allowlists GPU instance types
#     (g4dn/p3/p4d/p5/g5/g6) — the operator cannot launch non-GPU instances
#   — No iam:* or sts:* wildcards anywhere in the policy document
#   — DenyIamEscalation explicitly blocks role/user creation + policy attachment

terraform {
  required_version = ">= 1.4.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Account ID is derived from the caller so the user does not have to type it.
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Role — trust relationship
# ---------------------------------------------------------------------------
# Trust ONLY the EC2 service. The role is attached to GPU instances via the
# instance profile below, and the same role is what the user authenticates as
# (via access key or STS assume-role) to run `terraform apply`. We do not add
# cross-account trust or trust for other services — least-privilege.
#
# The trust policy is shipped as a separate JSON file (pure JSON, not HCL) so
# it is statically auditable.

resource "aws_iam_role" "compute_operator" {
  name               = var.role_name
  assume_role_policy = file("${path.module}/assume-role-policy.json")
  tags               = var.tags
}

# ---------------------------------------------------------------------------
# Instance profile — so the role can be attached to EC2 GPU instances.
# ---------------------------------------------------------------------------

resource "aws_iam_instance_profile" "compute_operator" {
  name = "${var.role_name}-profile"
  role = aws_iam_role.compute_operator.name

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Policy — the least-privilege security boundary.
# ---------------------------------------------------------------------------
# policy.json carries the full allow+deny document. templatefile() renders the
# operator role ARN and requested region into the IamPassRoleSelfOnly and EC2
# statements so PassRole is scoped to THIS role and compute is scoped to the
# selected deployment region.
#
# Allowlist rationale (per-action, mapped to the gludd feature that uses it):
#   ec2:RunInstances            — _generate_aws aws_instance resource creation
#   ec2:TerminateInstances      — ephemeral compute teardown / TTL watchdog
#   ec2:StartInstances          — start/stop lifecycle for cost control
#   ec2:StopInstances           — start/stop lifecycle for cost control
#   ec2:Describe*               — data.aws_ami lookups + drift/state reads
#   ec2:CreateSecurityGroup     — aws_security_group resource in _generate_aws
#   ec2:AuthorizeSecurityGroupIngress — ingress rule (port 8000) in _generate_aws
#   ec2:RevokeSecurityGroupIngress    — rule drift correction
#   ec2:DeleteSecurityGroup     — teardown of the security group
#   ec2:CreateVolume            — root_block_device expansion / EBS ops
#   ec2:AttachVolume            — EBS lifecycle
#   ec2:DetachVolume            — EBS lifecycle
#   ec2:DeleteVolume            — EBS teardown
#   ec2:AllocateAddress         — EIP for public endpoint (output.instance_ip)
#   ec2:AssociateAddress        — EIP attach
#   ec2:DisassociateAddress     — EIP teardown
#   ec2:ReleaseAddress          — EIP teardown
#   ec2:CreateTags              — aws_instance tags block in _generate_aws
#   ec2:DeleteTags              — tag cleanup on teardown
#   iam:PassRole                — required so EC2 can attach *this* role to the
#                                 instances it launches. Scoped to this role
#                                 only — see IamPassRoleSelfOnly.
#   logs:CreateLogGroup         — CloudWatch Logs for inference container stdout
#   logs:CreateLogStream        — per-instance log stream
#   logs:PutLogEvents           — log shipping
#
# SSM is intentionally absent: src/general_ludd/infra/terraform.py does not
# reference SSM, and adding ssm:* without a consumer would violate least
# privilege. Add it back ONLY if a gludd feature actually calls the SSM API.

locals {
  # Keep the source policy statically valid JSON while rendering both runtime
  # values through Terraform's standard templatefile boundary.
  policy_document = templatefile("${path.module}/policy.json", {
    operator_role_arn = aws_iam_role.compute_operator.arn
    operator_region   = var.region
  })
}

resource "aws_iam_policy" "compute_least_priv" {
  name        = var.policy_name
  description = "Least-privilege boundary for gludd compute operator. Pinned by tests/unit/test_onboard_aws.py."

  policy = local.policy_document
}

resource "aws_iam_role_policy_attachment" "compute_least_priv" {
  role       = aws_iam_role.compute_operator.name
  policy_arn = aws_iam_policy.compute_least_priv.arn
}
