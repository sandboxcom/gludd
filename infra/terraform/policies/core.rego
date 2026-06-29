# Core OPA policies for any terraform plan.
#
# These run as a system-level gate via `conftest test -p infra/terraform/policies/`.
# They are INTENTIONALLY MINIMAL: each rule catches a real misconfiguration that
# has caused real incidents. They MUST NOT fire on compliant standard work — if
# a rule starts firing on routine stacks, narrow it (do not delete it).
#
# Rego `deny` is an additive set — user-collection policies in
# `plugins/terraform/policies/*.rego` ADD reasons to this set; they cannot
# subtract (no `deny -= ...`; enforced by tests/unit/test_collection_terraform_layout.py).

package main

# Required tags per gludd tagging standard.
required_tags := {"Owner", "Project"}

# Public-ingress ports that are NEVER allowed from 0.0.0.0/0.
forbidden_public_ports := {22, 3389, 3306, 5432}

# ---------------------------------------------------------------------------
# Provider version pinning — required_providers.version must use ~> or = only.
# `>=`, `>`, `<=`, `<`, and bare versions are unpinning smells.
# terraform plan JSON surfaces this under configuration.provider_config.<name>.version_constraint
# ---------------------------------------------------------------------------
deny[level] {
	provider := input.configuration.provider_config[_]
	version := provider.version_constraint
	not version_is_pinned(version)
	level := sprintf("provider %q version %q is not pinned (use ~> or =)", [provider.name, version])
}

version_is_pinned(version) {
	startswith(version, "~>")
}

version_is_pinned(version) {
	startswith(version, "=")
}

# ---------------------------------------------------------------------------
# S3 bucket public-read ACL — default-deny.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	resource.type == "aws_s3_bucket"
	resource.values.acl == "public-read"
	level := sprintf("aws_s3_bucket.%s has acl=public-read (default-deny)", [resource.address])
}

deny[level] {
	module := input.planned_values.root_module.child_modules[_]
	resource := module.resources[_]
	resource.type == "aws_s3_bucket"
	resource.values.acl == "public-read"
	level := sprintf("aws_s3_bucket.%s has acl=public-read (default-deny)", [resource.address])
}

# ---------------------------------------------------------------------------
# Security group ingress from 0.0.0.0/0 on forbidden ports.
# Allows 80/443; denies 22/3389/3306/5432.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	resource.type == "aws_security_group"
	rule := resource.values.ingress[_]
	rule.cidr_blocks[_] == "0.0.0.0/0"
	port := rule.from_port
	forbidden_public_ports[port]
	level := sprintf("aws_security_group.%s allows 0.0.0.0/0 on port %d", [resource.address, port])
}

deny[level] {
	module := input.planned_values.root_module.child_modules[_]
	resource := module.resources[_]
	resource.type == "aws_security_group"
	rule := resource.values.ingress[_]
	rule.cidr_blocks[_] == "0.0.0.0/0"
	port := rule.from_port
	forbidden_public_ports[port]
	level := sprintf("aws_security_group.%s allows 0.0.0.0/0 on port %d", [resource.address, port])
}

# ---------------------------------------------------------------------------
# Missing required tags (Owner, Project) on any tagged resource type.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	tagged_resource_type(resource.type)
	tags := object.get(resource.values, "tags", {})
	not tags.Owner
	level := sprintf("%s.%s is missing tags.Owner (gludd tagging standard)", [resource.type, resource.address])
}

deny[level] {
	resource := input.planned_values.root_module.resources[_]
	tagged_resource_type(resource.type)
	tags := object.get(resource.values, "tags", {})
	not tags.Project
	level := sprintf("%s.%s is missing tags.Project (gludd tagging standard)", [resource.type, resource.address])
}

deny[level] {
	module := input.planned_values.root_module.child_modules[_]
	resource := module.resources[_]
	tagged_resource_type(resource.type)
	tags := object.get(resource.values, "tags", {})
	not tags.Owner
	level := sprintf("%s.%s is missing tags.Owner (gludd tagging standard)", [resource.type, resource.address])
}

# Resource types that MUST carry gludd tags.
tagged_resource_type(type) {
	startswith(type, "aws_")
}

tagged_resource_type(type) {
	startswith(type, "azurerm_")
}

tagged_resource_type(type) {
	startswith(type, "google_")
}

# terraform_data is the built-in no-provider resource — never tagged.
tagged_resource_type(type) {
	type != "terraform_data"
	type != "terraform_required_provider"
}

# ---------------------------------------------------------------------------
# RDS storage_encrypted=false.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	resource.type == "aws_db_instance"
	resource.values.storage_encrypted == false
	level := sprintf("aws_db_instance.%s has storage_encrypted=false", [resource.address])
}

# ---------------------------------------------------------------------------
# EC2 instance with associate_public_ip_address=true on non-bastion hosts.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	resource.type == "aws_instance"
	resource.values.associate_public_ip_address == true
	not is_bastion(resource)
	level := sprintf("aws_instance.%s has associate_public_ip_address=true on non-bastion", [resource.address])
}

is_bastion(resource) {
	tags := object.get(resource.values, "tags", {})
	name := tags.Name
	contains(lower(name), "bastion")
}

is_bastion(resource) {
	tags := object.get(resource.values, "tags", {})
	tags.Role == "bastion"
}

# ---------------------------------------------------------------------------
# Azure storage/account with enable_https_traffic_only=false.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	startswith(resource.type, "azurerm_storage_account")
	resource.values.enable_https_traffic_only == false
	level := sprintf("%s.%s has enable_https_traffic_only=false", [resource.type, resource.address])
}

# ---------------------------------------------------------------------------
# GCP compute instance without service_account.scopes constraint.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	resource.type == "google_compute_instance"
	sa := object.get(resource.values, "service_account", [])
	count(sa) == 0
	level := sprintf("google_compute_instance.%s has no service_account.scopes constraint", [resource.address])
}

deny[level] {
	resource := input.planned_values.root_module.resources[_]
	resource.type == "google_compute_instance"
	sa := object.get(resource.values, "service_account", [])
	count(sa) > 0
	scopes := object.get(sa[0], "scopes", [])
	count(scopes) == 0
	level := sprintf("google_compute_instance.%s service_account has empty scopes", [resource.address])
}

# ---------------------------------------------------------------------------
# AWS key id leak in any attribute value.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	value := walk_resource_values(resource)
	regex.match("AKIA[0-9A-Z]{16}", sprintf("%v", [value]))
	level := sprintf("%s.%s attribute contains AWS access key id (AKIA...)", [resource.type, resource.address])
}

# ---------------------------------------------------------------------------
# Private key leak in any attribute value.
# ---------------------------------------------------------------------------
deny[level] {
	resource := input.planned_values.root_module.resources[_]
	value := walk_resource_values(resource)
	regex.match("-----BEGIN.*PRIVATE KEY-----", sprintf("%v", [value]))
	level := sprintf("%s.%s attribute contains a private key header", [resource.type, resource.address])
}

# Helper: any scalar value found anywhere in resource.values. Rego does not
# support full recursive descent natively in <0.40, so we walk the known
# shallow keys and the deeper `tags`/`user_data`/`metadata` maps that real
# leaks hide in. This is intentionally broad — false positives here would
# block legitimate work, so it only fires on the literal AKIA/PEM markers.
walk_resource_values(resource) = v {
	v := object.get(resource.values, "user_data", "")
	v != ""
}

walk_resource_values(resource) = v {
	tags := object.get(resource.values, "tags", {})
	v := sprintf("%v", [tags])
}

walk_resource_values(resource) = v {
	v := object.get(resource.values, "metadata", "")
	v != ""
}
