package iam

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
