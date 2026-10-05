package iam_test

import data.iam.deny

test_deny_admin_access if {
	result := deny with input as {
		"Statement": [{
			"Effect": "Allow",
			"Action": ["*"],
			"Resource": ["*"]
		}]
	}
	count(result) == 1
}

test_allow_scoped_policy if {
	result := deny with input as {
		"Statement": [{
			"Effect": "Allow",
			"Action": ["ec2:Describe*", "s3:GetObject"],
			"Resource": ["arn:aws:s3:::my-bucket/*"]
		}]
	}
	count(result) == 0
}

test_deny_wildcard_rds if {
	result := deny with input as {
		"Statement": [{
			"Effect": "Allow",
			"Action": ["rds:*"],
			"Resource": ["*"]
		}]
	}
	count(result) == 1
	contains(result[_], "wildcard")
}

test_deny_mfa_missing_for_create_user if {
	result := deny with input as {
		"Statement": [{
			"Effect": "Allow",
			"Action": ["iam:CreateUser"],
			"Resource": ["*"],
			"Condition": {"Bool": {}}
		}]
	}
	count(result) == 1
}

test_allow_create_user_with_mfa if {
	result := deny with input as {
		"Statement": [{
			"Effect": "Allow",
			"Action": ["iam:CreateUser"],
			"Resource": ["*"],
			"Condition": {"Bool": {"aws:MultiFactorAuthPresent": "true"}}
		}]
	}
	count(result) == 0
}

test_aws_least_privilege_valid if {
	data.iam.aws_least_privilege_valid with input as {
		"provider": "aws",
		"Statement": [{
			"Effect": "Allow",
			"Action": ["ec2:RunInstances"],
			"Resource": ["arn:aws:ec2:us-east-1:123456789012:instance/*"]
		}]
	}
}

test_azure_scope_is_required if {
	result := data.iam.deny_azure_missing_scope with input as {
		"provider": "azure",
		"role_assignments": [{"role": "Virtual Machine Contributor"}]
	}
	count(result) == 1
}

test_azure_scoped_assignment_is_valid if {
	data.iam.azure_least_privilege_valid with input as {
		"provider": "azure",
		"role_assignments": [{"scope": "/subscriptions/sub-123/resourceGroups/gludd-models"}]
	}
}

test_azure_missing_name_denied if {
	result := data.iam.deny_azure_missing_metadata with input as {
		"provider": "azure",
		"Name": "",
		"Description": "A valid description",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 1
}

test_azure_missing_description_denied if {
	result := data.iam.deny_azure_missing_metadata with input as {
		"provider": "azure",
		"Name": "ValidName",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 1
}

test_azure_metadata_present_passes if {
	result := data.iam.deny_azure_missing_metadata with input as {
		"provider": "azure",
		"Name": "ValidName",
		"Description": "A valid description",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 0
}

test_azure_actions_empty_denied if {
	result := data.iam.deny_azure_no_actions with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": [],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 1
}

test_azure_actions_non_empty_allowed if {
	result := data.iam.deny_azure_no_actions with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 0
}

test_azure_runcommand_grant_fails if {
	result := data.iam.deny_azure_forbidden_action with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
		"NotActions": [],
		"AssignableScopes": ["/subscriptions/sub-123/resourceGroups/gludd-models"]
	}
	count(result) == 1
}

test_azure_runcommand_absent_passes if {
	result := data.iam.deny_azure_forbidden_action with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"NotActions": [],
		"AssignableScopes": ["/subscriptions/sub-123/resourceGroups/gludd-models"]
	}
	count(result) == 0
}

test_azure_roleassignment_grant_fails if {
	result := data.iam.deny_azure_forbidden_action with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": [
			"Microsoft.Authorization/roleAssignments/write",
			"Microsoft.Authorization/roleAssignments/delete"
		],
		"NotActions": [],
		"AssignableScopes": ["/subscriptions/sub-123/resourceGroups/gludd-models"]
	}
	count(result) == 2
}

test_azure_roleassignment_absent_passes if {
	result := data.iam.deny_azure_forbidden_action with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"NotActions": [],
		"AssignableScopes": ["/subscriptions/sub-123/resourceGroups/gludd-models"]
	}
	count(result) == 0
}

test_azure_list_action_suffix_denied if {
	result := data.iam.deny_azure_list_action_suffix with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": [
			"Microsoft.Storage/storageAccounts/listKeys/list/action",
			"Microsoft.Compute/virtualMachines/read"
		],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 1
}

test_azure_no_list_action_suffix_passes if {
	result := data.iam.deny_azure_list_action_suffix with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": [
			"Microsoft.ContainerRegistry/registries/listCredentials/action",
			"Microsoft.Compute/virtualMachines/read"
		],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 0
}

test_azure_data_plane_denied if {
	result := data.iam.deny_azure_data_plane_access with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"DataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 1
}

test_azure_data_actions_empty_passes if {
	result := data.iam.deny_azure_data_plane_access with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"DataActions": [],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 0
}

test_azure_invalid_scope_denied if {
	result := data.iam.deny_azure_invalid_scope with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": ["/"]
	}
	count(result) == 1
}

test_azure_subscription_scope_is_too_broad if {
	result := data.iam.deny_azure_invalid_scope with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": ["/subscriptions/sub-123"]
	}
	count(result) == 1
}

test_azure_accelerator_role_resource_group_scope_passes if {
	result := data.iam.deny_azure_invalid_scope with input as {
		"provider": "azure",
		"Name": "General Ludd Accelerator Deployer",
		"Actions": ["Microsoft.App/containerApps/read"],
		"AssignableScopes": ["/subscriptions/sub-123/resourceGroups/gludd-models"]
	}
	count(result) == 0
}

test_azure_missing_scopes_denied if {
	result := data.iam.deny_azure_missing_assignable_scopes with input as {
		"provider": "azure",
		"Name": "TestRole",
		"Actions": ["Microsoft.Compute/virtualMachines/read"],
		"AssignableScopes": []
	}
	count(result) == 1
}

test_azure_custom_role_valid if {
	data.iam.azure_custom_role_valid with input as {
		"provider": "azure",
		"Name": "General Ludd Accelerator Deployer",
		"Description": "Least-privilege role for one Container Apps GPU environment",
		"AssignableScopes": ["/subscriptions/sub-123/resourceGroups/gludd-models"],
		"Actions": [
			"Microsoft.App/managedEnvironments/read",
			"Microsoft.App/managedEnvironments/join/action",
			"Microsoft.App/managedEnvironments/usages/read",
			"Microsoft.App/managedEnvironments/workloadProfileStates/read",
			"Microsoft.App/containerApps/read",
			"Microsoft.App/containerApps/write",
			"Microsoft.App/containerApps/delete",
			"Microsoft.App/containerApps/revisions/read",
			"Microsoft.App/locations/containerAppOperationResults/read",
			"Microsoft.App/locations/containerAppOperationStatuses/read"
		],
		"NotActions": [],
		"DataActions": [],
		"NotDataActions": []
	}
}

test_gcp_setmetadata_is_denied if {
	result := data.iam.deny_gcp_set_metadata with input as {
		"provider": "gcp",
		"permissions": ["compute.instances.setMetadata"]
	}
	count(result) == 1
}

test_all_clouds_requires_each_provider if {
	data.iam.all_clouds_least_privilege_valid with input as {
		"providers": {"aws": true, "azure": true, "gcp": true}
	}
}
