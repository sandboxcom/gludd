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
