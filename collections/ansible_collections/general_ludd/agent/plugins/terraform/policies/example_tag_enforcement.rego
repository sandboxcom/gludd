# Example user policy: enforce an Environment tag on every AWS resource.
#
# Loaded by `conftest test -p infra/terraform/policies/ -p <collection>/plugins/terraform/policies/`.
# Adds deny rules to the same `deny[level]` set as trust.rego / any core.rego.
# The importer runs `opa check` on this file and rejects set reassignment
# (`deny := ...`, `deny -= ...`); only additive rules are permitted.
#
# Input shape MUST match terraform-plan JSON, same as core.rego:
#   input.planned_values.root_module.resources[_]
#   input.planned_values.root_module.child_modules[_].resources[_]

package main

# AWS resources at the root module must carry a tags.Environment key.
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	startswith(resource.type, "aws_")
	tags := object.get(resource.values, "tags", {})
	not tags.Environment
	level := sprintf("%s.%s is missing tags.Environment", [resource.type, resource.address])
}

# AWS resources in child modules must carry a tags.Environment key.
deny[level] {
	module := input.planned_values.root_module.child_modules[_]
	resource := module.resources[_]
	startswith(resource.type, "aws_")
	tags := object.get(resource.values, "tags", {})
	not tags.Environment
	level := sprintf("%s.%s is missing tags.Environment", [resource.type, resource.address])
}
