"""Tests for GCP IAM validator — validate_gcp_role and module-level constants."""

from __future__ import annotations

from general_ludd.cloud.gcp_validator import (
    GCP_DANGEROUS_PERMISSIONS,
    GCP_DANGEROUS_ROLES,
    GCP_REQUIRED_DENIALS,
    validate_gcp_role,
)


class TestGcpConstants:
    def test_dangerous_roles_includes_owner_editor(self):
        assert "roles/owner" in GCP_DANGEROUS_ROLES
        assert "roles/editor" in GCP_DANGEROUS_ROLES

    def test_dangerous_roles_includes_security_admin(self):
        assert "roles/iam.securityAdmin" in GCP_DANGEROUS_ROLES

    def test_dangerous_permissions_includes_set_metadata(self):
        assert "compute.instances.setMetadata" in GCP_DANGEROUS_PERMISSIONS

    def test_dangerous_permissions_includes_act_as(self):
        assert "iam.serviceAccounts.actAs" in GCP_DANGEROUS_PERMISSIONS

    def test_dangerous_permissions_includes_set_iam_policy(self):
        assert "iam.serviceAccounts.setIamPolicy" in GCP_DANGEROUS_PERMISSIONS

    def test_dangerous_permissions_does_not_include_safe_perm(self):
        assert "storage.objects.get" not in GCP_DANGEROUS_PERMISSIONS

    def test_dangerous_roles_is_frozenset(self):
        assert isinstance(GCP_DANGEROUS_ROLES, frozenset)

    def test_dangerous_permissions_is_frozenset(self):
        assert isinstance(GCP_DANGEROUS_PERMISSIONS, frozenset)

    def test_required_denials_has_runtime_execution(self):
        assert "runtime_execution" in GCP_REQUIRED_DENIALS
        assert isinstance(GCP_REQUIRED_DENIALS["runtime_execution"], list)

    def test_required_denials_runtime_includes_set_iam_policy(self):
        denied = GCP_REQUIRED_DENIALS["runtime_execution"]
        assert "iam.serviceAccounts.setIamPolicy" in denied

    def test_required_denials_runtime_includes_act_as(self):
        denied = GCP_REQUIRED_DENIALS["runtime_execution"]
        assert "iam.serviceAccounts.actAs" in denied

    def test_required_denials_terraform_deploy_is_empty(self):
        assert GCP_REQUIRED_DENIALS["terraform_deploy"] == []

    def test_required_denials_model_inference_includes_set_metadata(self):
        denied = GCP_REQUIRED_DENIALS["model_inference"]
        assert "compute.instances.setMetadata" in denied

    def test_required_denials_monitor_includes_create_account(self):
        denied = GCP_REQUIRED_DENIALS["monitor"]
        assert "iam.serviceAccounts.create" in denied
        assert "iam.serviceAccountKeys.create" in denied


class TestValidateGcpRole:
    def test_empty_bindings_returns_invalid(self):
        result = validate_gcp_role("runtime_execution", [])
        assert result["status"] == "invalid"
        assert "non-empty" in result["errors"][0]

    def test_non_list_bindings_returns_invalid(self):
        result = validate_gcp_role("runtime_execution", "not-a-list")
        assert result["status"] == "invalid"

    def test_non_dict_binding_reports_error(self):
        bindings = ["not a dict", {"role": "roles/viewer"}]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("not a dict" in e for e in result["errors"])

    def test_dangerous_role_owner_flagged(self):
        bindings = [{"role": "roles/owner", "members": ["user:a@b.c"]}]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("roles/owner" in e for e in result["errors"])

    def test_dangerous_role_editor_flagged(self):
        bindings = [{"role": "roles/editor", "members": ["user:a@b.c"]}]
        result = validate_gcp_role("terraform_deploy", bindings)
        assert result["status"] == "invalid"
        assert any("roles/editor" in e for e in result["errors"])

    def test_dangerous_role_security_admin_flagged(self):
        bindings = [{"role": "roles/iam.securityAdmin"}]
        result = validate_gcp_role("model_inference", bindings)
        assert result["status"] == "invalid"
        assert any("roles/iam.securityAdmin" in e for e in result["errors"])

    def test_safe_custom_role_not_flagged(self):
        bindings = [{"role": "roles/custom.viewer", "permissions": ["storage.objects.get"]}]
        result = validate_gcp_role("monitor", bindings)
        assert result["status"] == "valid"

    def test_set_metadata_without_condition_flagged(self):
        bindings = [{"permissions": ["compute.instances.setMetadata"]}]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("setMetadata" in e for e in result["errors"])

    def test_set_metadata_with_cel_condition_warns(self):
        bindings = [
            {
                "permissions": ["compute.instances.setMetadata"],
                "condition": {"expression": "resource.name.startsWith('prod')"},
            }
        ]
        result = validate_gcp_role("terraform_deploy", bindings)
        assert any("CEL condition" in w for w in result["warnings"])

    def test_set_service_account_without_condition_flagged(self):
        bindings = [{"permissions": ["compute.instances.setServiceAccount"]}]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("setServiceAccount" in e for e in result["errors"])

    def test_runtime_execution_must_deny_set_iam_policy(self):
        bindings = [{"permissions": ["iam.serviceAccounts.setIamPolicy"], "effect": "allow"}]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("must deny" in e for e in result["errors"])

    def test_runtime_execution_deny_satisfies_requirement(self):
        bindings = [
            {"permissions": ["iam.serviceAccounts.setIamPolicy"], "effect": "deny"},
        ]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "valid"

    def test_runtime_execution_missing_deny_binding_warns(self):
        bindings = [{"permissions": ["storage.objects.get"], "effect": "allow"}]
        result = validate_gcp_role("runtime_execution", bindings)
        assert any("no explicit Deny" in w for w in result["warnings"])

    def test_runtime_execution_with_deny_does_not_warn(self):
        bindings = [
            {"permissions": ["storage.objects.get"], "effect": "allow"},
            {"permissions": ["compute.instances.setMetadata"], "effect": "deny"},
        ]
        result = validate_gcp_role("runtime_execution", bindings)
        assert not any("no explicit Deny" in w for w in result["warnings"])

    def test_persona_not_in_required_denials_skips_check(self):
        bindings = [{"permissions": ["compute.instances.setMetadata"]}]
        result = validate_gcp_role("unknown_persona", bindings)
        assert "Persona" not in str(result["errors"])

    def test_terraform_deploy_no_required_denials(self):
        bindings = [{"permissions": ["compute.instances.setMetadata"]}]
        result = validate_gcp_role("terraform_deploy", bindings)
        assert result["status"] == "invalid"

    def test_model_inference_deny_set_metadata_satisfies(self):
        bindings = [
            {"permissions": ["compute.instances.setMetadata"], "effect": "deny"},
        ]
        result = validate_gcp_role("model_inference", bindings)
        assert result["status"] == "valid"

    def test_model_inference_missing_deny_for_act_as(self):
        bindings = [
            {"permissions": ["compute.instances.setMetadata"], "effect": "deny"},
            {"permissions": ["iam.serviceAccounts.actAs"], "effect": "allow"},
        ]
        result = validate_gcp_role("model_inference", bindings)
        assert result["status"] == "invalid"
        assert any("actAs" in e for e in result["errors"])

    def test_monitor_deny_create_required(self):
        bindings = [{"permissions": ["iam.serviceAccounts.create"], "effect": "allow"}]
        result = validate_gcp_role("monitor", bindings)
        assert result["status"] == "invalid"
        assert any("must deny" in e for e in result["errors"])

    def test_empty_condition_dict_treated_as_missing(self):
        bindings = [
            {
                "permissions": ["compute.instances.setMetadata"],
                "condition": {},
            }
        ]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("CEL condition" in e for e in result["errors"])

    def test_condition_with_expression_but_not_dict_still_flagged(self):
        bindings = [
            {
                "permissions": ["compute.instances.setMetadata"],
                "condition": "not-a-dict",
            }
        ]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "invalid"
        assert any("CEL condition" in e for e in result["errors"])

    def test_all_permissions_correctly_denied_for_runtime(self):
        bindings = [
            {
                "permissions": [
                    "iam.serviceAccounts.setIamPolicy",
                    "iam.serviceAccounts.actAs",
                    "compute.instances.setMetadata",
                    "compute.instances.setServiceAccount",
                ],
                "effect": "deny",
            }
        ]
        result = validate_gcp_role("runtime_execution", bindings)
        assert result["status"] == "valid"
        assert not result["errors"]

    def test_valid_binding_returns_valid_status(self):
        bindings = [{"role": "roles/custom.viewer", "permissions": ["storage.objects.get"], "members": ["user:a@b.c"]}]
        result = validate_gcp_role("terraform_deploy", bindings)
        assert result["status"] == "valid"
        assert result["errors"] == []

    def test_multiple_bindings_mixed_effects(self):
        bindings = [
            {"permissions": ["storage.objects.get"], "effect": "allow"},
            {"permissions": ["compute.instances.setMetadata", "iam.serviceAccounts.actAs"], "effect": "deny"},
        ]
        result = validate_gcp_role("model_inference", bindings)
        assert result["status"] == "valid"

    def test_result_always_has_status_errors_warnings_keys(self):
        result = validate_gcp_role("runtime_execution", [{"role": "roles/viewer"}])
        assert "status" in result
        assert "errors" in result
        assert "warnings" in result
        assert isinstance(result["errors"], list)
        assert isinstance(result["warnings"], list)
