package terraform_test

import data.terraform.deny

test_deny_untagged_resource if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_instance.web",
			"type": "aws_instance",
			"change": {
				"actions": ["create"],
				"after": {"tags": null}
			}
		}]
	}
	count(result) == 1
	contains(result[_], "must have tags")
}

test_allow_tagged_resource if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_instance.web",
			"type": "aws_instance",
			"change": {
				"actions": ["create"],
				"after": {"tags": {"Name": "web"}}
			}
		}]
	}
	count(result) == 0
}

test_allow_existing_resource_no_tags if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_instance.old",
			"type": "aws_instance",
			"change": {
				"actions": ["update"],
				"after": {"tags": null}
			}
		}]
	}
	count(result) == 0
}

test_deny_security_group_rule_no_description if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_security_group_rule.open",
			"type": "aws_security_group_rule",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"cidr_blocks": ["0.0.0.0/0"],
					"description": null
				}
			}
		}]
	}
	count(result) == 1
	contains(result[_], "0.0.0.0/0 with no description")
}

test_allow_security_group_rule_with_description if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_security_group_rule.described",
			"type": "aws_security_group_rule",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"cidr_blocks": ["0.0.0.0/0"],
					"description": "Allow external monitoring"
				}
			}
		}]
	}
	count(result) == 0
}

test_deny_s3_public_acls_not_blocked if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_s3_bucket_public_access_block.bad",
			"type": "aws_s3_bucket_public_access_block",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"block_public_acls": false
				}
			}
		}]
	}
	count(result) == 1
	contains(result[_], "must block public ACLs")
}

test_allow_s3_block_public_acls if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_s3_bucket_public_access_block.good",
			"type": "aws_s3_bucket_public_access_block",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"block_public_acls": true
				}
			}
		}]
	}
	count(result) == 0
}

test_deny_rds_no_encryption if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_db_instance.bad",
			"type": "aws_db_instance",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"storage_encrypted": false
				}
			}
		}]
	}
	count(result) == 1
	contains(result[_], "storage encryption enabled")
}

test_allow_rds_encrypted if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_db_instance.good",
			"type": "aws_db_instance",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"storage_encrypted": true
				}
			}
		}]
	}
	count(result) == 0
}

test_deny_iam_wildcard_action if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_iam_policy.bad",
			"type": "aws_iam_policy",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"policy": "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"*\",\"Resource\":\"*\"}]}"
				}
			}
		}]
	}
	count(result) == 1
	contains(result[_], "wildcard Action:*")
}

test_allow_iam_scoped_policy if {
	result := deny with input as {
		"resource_changes": [{
			"address": "aws_iam_policy.good",
			"type": "aws_iam_policy",
			"change": {
				"actions": ["create"],
				"after": {
					"tags": {"Name": "test"},
					"policy": "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ec2:DescribeInstances\"],\"Resource\":\"*\"}]}"
				}
			}
		}]
	}
	count(result) == 0
}
