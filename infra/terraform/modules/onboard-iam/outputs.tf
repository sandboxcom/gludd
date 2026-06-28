# Outputs for the gludd onboarding IAM module.
# `role_arn` is the value `gludd onboard aws` asks the user to copy into
# ~/.config/gludd/onboarded-provider.json (via the CLI scaffold).

output "role_arn" {
  description = "ARN of the gludd compute-operator IAM role. Pass this to `gludd onboard --role-arn`."
  value       = aws_iam_role.compute_operator.arn
}

output "role_name" {
  description = "Name of the IAM role."
  value       = aws_iam_role.compute_operator.name
}

output "instance_profile_arn" {
  description = "ARN of the EC2 instance profile that carries the operator role."
  value       = aws_iam_instance_profile.compute_operator.arn
}

output "instance_profile_name" {
  description = "Name of the EC2 instance profile. Attach this to gpu instances in _generate_aws."
  value       = aws_iam_instance_profile.compute_operator.name
}
