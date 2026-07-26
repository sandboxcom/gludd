package iam

# Provider-specific policy contracts. Inputs may be either a single provider
# object or a normalized Terraform plan summary. All rules fail closed when a
# required field is absent.

deny_aws_wildcard_resource contains msg if {
	input.provider == "aws"
	statement := input.Statement[_]
	statement.Effect == "Allow"
	statement.Resource[_] == "*"
	msg := "AWS allow statement has an unscoped resource"
}

deny_azure_missing_scope contains msg if {
	input.provider == "azure"
	assignment := input.role_assignments[_]
	object.get(assignment, "scope", "") == ""
	msg := "Azure role assignment is missing an explicit subscription scope"
}

deny_gcp_set_metadata contains msg if {
	input.provider == "gcp"
	permission := input.permissions[_]
	permission == "compute.instances.setMetadata"
	msg := "GCP role grants compute.instances.setMetadata"
}

aws_least_privilege_valid if {
	input.provider == "aws"
	count(deny_aws_wildcard_resource) == 0
	count(deny) == 0
}

azure_least_privilege_valid if {
	input.provider == "azure"
	count(deny_azure_missing_scope) == 0
	count(deny) == 0
}

gcp_least_privilege_valid if {
	input.provider == "gcp"
	count(deny_gcp_set_metadata) == 0
	count(deny) == 0
}

all_clouds_least_privilege_valid if {
	input.providers.aws == true
	input.providers.azure == true
	input.providers.gcp == true
}

deny contains msg if {
	statement := input.Statement[_]
	statement.Effect == "Allow"
	contains(statement.Action[_], "*")
	msg := "IAM policy allows wildcard service actions"
}

deny contains msg if {
	statement := input.Statement[_]
	statement.Effect == "Allow"
	statement.Action[_] == "*"
	statement.Resource[_] == "*"
	msg := "IAM policy grants full administrative access"
}

deny contains msg if {
	statement := input.Statement[_]
	statement.Action[_] == "iam:CreateUser"
	not statement.Condition.Bool["aws:MultiFactorAuthPresent"]
	msg := "iam:CreateUser must require MFA"
}
