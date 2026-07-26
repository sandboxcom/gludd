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
		"role_assignments": [{"scope": "/subscriptions/sub-123"}]
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
