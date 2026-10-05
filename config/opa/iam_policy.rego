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
	msg := "Azure role assignment is missing an explicit resource-group scope"
}

deny_gcp_set_metadata contains msg if {
	input.provider == "gcp"
	permission := input.permissions[_]
	permission == "compute.instances.setMetadata"
	msg := "GCP role grants compute.instances.setMetadata"
}

deny_azure_no_actions contains msg if {
	input.provider == "azure"
	actions := object.get(input, "Actions", [])
	count(actions) == 0
	msg := "Azure custom role has an empty Actions list"
}

deny_azure_missing_metadata contains msg if {
	input.provider == "azure"
	object.get(input, "Name", "") == ""
	msg := "Azure custom role is missing a Name"
}

deny_azure_missing_metadata contains msg if {
	input.provider == "azure"
	object.get(input, "Description", "") == ""
	msg := "Azure custom role is missing a Description"
}

deny_azure_missing_assignable_scopes contains msg if {
	input.provider == "azure"
	scopes := object.get(input, "AssignableScopes", [])
	count(scopes) == 0
	msg := "Azure custom role has empty AssignableScopes"
}

deny_azure_invalid_scope contains msg if {
	input.provider == "azure"
	scopes := object.get(input, "AssignableScopes", [])
	some scope in scopes
	not regex.match("^/subscriptions/[^/]+/resourceGroups/[^/]+$", scope)
	msg := sprintf("Azure custom role scope is not a resource-group scope: %s", [scope])
}

azure_forbidden_actions := {
	"Microsoft.App/containerApps/listSecrets/action",
	"Microsoft.Authorization/roleAssignments/write",
	"Microsoft.Authorization/roleAssignments/delete",
	"Microsoft.Authorization/roleDefinitions/write",
	"Microsoft.Authorization/roleDefinitions/delete",
	"Microsoft.Compute/virtualMachines/runCommand/action",
	"Microsoft.Compute/virtualMachines/runCommands/read",
	"Microsoft.Compute/virtualMachines/runCommands/write",
	"Microsoft.Compute/virtualMachines/runCommands/delete",
}

deny_azure_forbidden_action contains msg if {
	input.provider == "azure"
	action := input.Actions[_]
	action in azure_forbidden_actions
	msg := sprintf("Azure custom role grants forbidden action: %s", [action])
}

deny_azure_list_action_suffix contains msg if {
	input.provider == "azure"
	actions := object.get(input, "Actions", [])
	some action in actions
	contains(action, "/list/action")
	msg := sprintf("Azure custom role uses /list/action suffix: %s; use /read instead", [action])
}

deny_azure_data_plane_access contains msg if {
	input.provider == "azure"
	data_actions := object.get(input, "DataActions", [])
	count(data_actions) > 0
	msg := "Azure custom role has non-empty DataActions"
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

azure_custom_role_valid if {
	input.provider == "azure"
	count(deny_azure_no_actions) == 0
	count(deny_azure_missing_metadata) == 0
	count(deny_azure_missing_assignable_scopes) == 0
	count(deny_azure_invalid_scope) == 0
	count(deny_azure_forbidden_action) == 0
	count(deny_azure_list_action_suffix) == 0
	count(deny_azure_data_plane_access) == 0
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
	some action in statement.Action
	endswith(action, ":*")
	msg := "IAM policy allows wildcard service actions"
}

deny contains msg if {
	statement := input.Statement[_]
	statement.Effect == "Allow"
	some action in statement.Action
	endswith(action, ":*")
	statement.Resource[_] == "*"
	msg := "IAM policy allows wildcard service actions"
}

deny contains msg if {
	statement := input.Statement[_]
	statement.Effect == "Allow"
	statement.Action == ["*"]
	statement.Resource[_] == "*"
	msg := "IAM policy grants full administrative access"
}

deny contains msg if {
	statement := input.Statement[_]
	statement.Action[_] == "iam:CreateUser"
	not statement.Condition.Bool["aws:MultiFactorAuthPresent"]
	msg := "iam:CreateUser must require MFA"
}
