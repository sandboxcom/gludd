package terraform

deny contains msg if {
	resource := input.resource_changes[_]
	resource.change.actions[_] == "create"
	not is_object(resource.change.after.tags)
	msg := sprintf("Resource %s must have tags", [resource.address])
}

deny contains msg if {
	resource := input.resource_changes[_]
	resource.type == "aws_security_group_rule"
	resource.change.after.cidr_blocks[_] == "0.0.0.0/0"
	not is_string(resource.change.after.description)
	msg := sprintf("Security group rule %s allows 0.0.0.0/0 with no description", [resource.address])
}

deny contains msg if {
	resource := input.resource_changes[_]
	resource.type == "aws_s3_bucket_public_access_block"
	resource.change.after.block_public_acls != true
	msg := "S3 bucket must block public ACLs"
}

deny contains msg if {
	resource := input.resource_changes[_]
	resource.type == "aws_db_instance"
	resource.change.after.storage_encrypted != true
	msg := "RDS instance must have storage encryption enabled"
}

deny contains msg if {
	resource := input.resource_changes[_]
	resource.type == "aws_iam_policy"
	policy := resource.change.after.policy
	is_object(policy)
	statement := policy.Statement[_]
	statement.Effect == "Allow"
	statement.Action == ["*"]
	msg := sprintf("IAM policy %s uses wildcard Action:*", [resource.address])
}

deny contains msg if {
	resource := input.resource_changes[_]
	resource.type == "aws_iam_policy"
	raw_policy := resource.change.after.policy
	is_string(raw_policy)
	policy := json.unmarshal(raw_policy)
	statement := policy.Statement[_]
	statement.Effect == "Allow"
	statement.Action == ["*"]
	msg := sprintf("IAM policy %s uses wildcard Action:*", [resource.address])
}

deny contains msg if {
	resource := input.resource_changes[_]
	resource.type == "aws_iam_policy"
	raw_policy := resource.change.after.policy
	is_string(raw_policy)
	policy := json.unmarshal(raw_policy)
	statement := policy.Statement[_]
	statement.Effect == "Allow"
	statement.Action == "*"
	msg := sprintf("IAM policy %s uses wildcard Action:*", [resource.address])
}
