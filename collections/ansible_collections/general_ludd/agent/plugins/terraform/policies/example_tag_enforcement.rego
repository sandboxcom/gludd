# Example user policy: enforce an Environment tag on every AWS resource.
#
# Loaded by `conftest test -p infra/terraform/policies/ -p <collection>/plugins/terraform/policies/`.
# Adds deny rules to the same `deny[level]` set as trust.rego / any core.rego.
# The importer runs `opa check` on this file and rejects set reassignment
# (`deny := ...`, `deny -= ...`); only additive rules are permitted.

package main

# AWS resources must carry a tags.Environment key.
deny[level] {
	resource := input.resource[_]
	startswith(resource.type, "aws_")
	not resource.values.tags.Environment
	level := sprintf("%s %q is missing tags.Environment", [resource.type, resource.name])
}
