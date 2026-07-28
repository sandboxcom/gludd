package hottentot.iam

import future.keywords.in
import future.keywords.contains
import future.keywords.if

# ============================================================================
# AWS IAM least-privilege policy validation.
# Input: input.aws_policy.statements — list of {sid, effect, action, resource}
# ============================================================================

aws_allow_statements contains stmt if {
    stmt := input.aws_policy.statements[_]
    stmt.effect == "Allow"
}

deny_aws_wildcard_action contains msg if {
    stmt := aws_allow_statements[_]
    action := stmt.action[_]
    regex.match(".*\\*.*", action)
    msg := sprintf("AWS statement %s has wildcard action '%s'", [stmt.sid, action])
}

aws_actions_requiring_wildcard_resource := {
    "ec2:DescribeInstances",
    "ec2:DescribeInstanceStatus",
    "ec2:DescribeImages",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeSubnets",
    "ec2:DescribeVpcs",
    "ec2:DescribeVolumes",
}

deny_aws_wildcard_resource contains msg if {
    stmt := aws_allow_statements[_]
    resource := stmt.resource[_]
    resource == "*"
    action := stmt.action[_]
    not action in aws_actions_requiring_wildcard_resource
    msg := sprintf(
        "AWS statement %s has wildcard resource '*' for resource-scopable action '%s'",
        [stmt.sid, action],
    )
}

iam_escalation_actions := {
    "iam:CreateUser",
    "iam:CreateRole",
    "iam:AttachRolePolicy",
    "iam:PutRolePolicy",
    "iam:CreatePolicy",
    "iam:CreatePolicyVersion",
    "iam:SetDefaultPolicyVersion",
    "iam:UpdateAssumeRolePolicy",
    "iam:AddUserToGroup",
    "iam:PutUserPolicy",
    "iam:PutGroupPolicy",
    "iam:AttachUserPolicy",
    "iam:DetachRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:CreateAccessKey",
    "iam:CreateLoginProfile",
}

deny_aws_iam_escalation contains msg if {
    stmt := aws_allow_statements[_]
    action := stmt.action[_]
    action in iam_escalation_actions
    msg := sprintf("AWS statement %s allows IAM escalation action '%s'", [stmt.sid, action])
}

aws_least_privilege_valid if {
    count(deny_aws_wildcard_action) == 0
    count(deny_aws_wildcard_resource) == 0
    count(deny_aws_iam_escalation) == 0
}

# ============================================================================
# Azure IAM least-privilege validation.
# Input: input.azure_role_assignments — list of {role_definition_name, scope}
# ============================================================================

forbidden_azure_roles := {
    "Contributor",
    "Owner",
    "User Access Administrator",
}

deny_azure_forbidden_role contains msg if {
    assignment := input.azure_role_assignments[_]
    role := assignment.role_definition_name
    role in forbidden_azure_roles
    msg := sprintf("Azure role assignment uses forbidden role '%s'", [role])
}

deny_azure_missing_scope contains msg if {
    assignment := input.azure_role_assignments[_]
    scope := object.get(assignment, "scope", "")
    scope == ""
    msg := "Azure role assignment missing scope — billing account is uncontrolled"
}

azure_least_privilege_valid if {
    count(deny_azure_forbidden_role) == 0
    count(deny_azure_missing_scope) == 0
}

# ============================================================================
# GCP IAM least-privilege validation.
# Input: input.gcp_role_bindings — list of {role, permissions}
# ============================================================================

forbidden_gcp_roles := {
    "roles/owner",
    "roles/editor",
    "roles/viewer",
}

deny_gcp_forbidden_role contains msg if {
    binding := input.gcp_role_bindings[_]
    role := binding.role
    role in forbidden_gcp_roles
    msg := sprintf("GCP role binding uses forbidden role '%s'", [role])
}

deny_gcp_wildcard_permission contains msg if {
    binding := input.gcp_role_bindings[_]
    permissions := object.get(binding, "permissions", [])
    permission := permissions[_]
    regex.match(".*\\*.*", permission)
    msg := sprintf("GCP role '%s' has wildcard permission '%s'", [binding.role, permission])
}

deny_gcp_set_metadata contains msg if {
    binding := input.gcp_role_bindings[_]
    permissions := object.get(binding, "permissions", [])
    "compute.instances.setMetadata" in {p | p := permissions[_]}
    msg := sprintf("GCP role '%s' grants compute.instances.setMetadata — SSH key injection risk", [binding.role])
}

gcp_least_privilege_valid if {
    count(deny_gcp_forbidden_role) == 0
    count(deny_gcp_wildcard_permission) == 0
    count(deny_gcp_set_metadata) == 0
}

# ============================================================================
# Cross-cloud aggregate
# ============================================================================

all_clouds_least_privilege_valid if {
    aws_least_privilege_valid
    azure_least_privilege_valid
    gcp_least_privilege_valid
}
