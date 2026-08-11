"""Tests for cloud.core — IAM role generation and validation dispatch."""

from __future__ import annotations

from unittest import mock

from general_ludd.cloud.core import (
    CROSS_PROVIDER_PATTERNS,
    SUPPORTED_PROVIDERS,
    _check_cross_provider,
    generate_cloud_role,
    validate_cloud_role,
)


class TestSupportedProviders:
    def test_contains_three_providers(self) -> None:
        assert frozenset({"azure", "aws", "gcp"}) == SUPPORTED_PROVIDERS

    def test_is_frozenset(self) -> None:
        assert isinstance(SUPPORTED_PROVIDERS, frozenset)


class TestCrossProviderPatterns:
    def test_contains_expected_patterns(self) -> None:
        assert "wildcard_resource_all" in CROSS_PROVIDER_PATTERNS
        assert "owner_role_assignment" in CROSS_PROVIDER_PATTERNS
        assert "missing_deny_block" in CROSS_PROVIDER_PATTERNS
        assert "write_access_on_root" in CROSS_PROVIDER_PATTERNS
        assert "secret_key_read_not_action" in CROSS_PROVIDER_PATTERNS
        assert "runcommand_allowed" in CROSS_PROVIDER_PATTERNS
        assert "passrole_unscoped" in CROSS_PROVIDER_PATTERNS
        assert "fulladmin_managed_policy" in CROSS_PROVIDER_PATTERNS
        assert "no_condition_on_sensitive" in CROSS_PROVIDER_PATTERNS


class TestGenerateCloudRole:
    def test_unsupported_provider(self) -> None:
        result = generate_cloud_role("oracle", "admin")
        assert result["status"] == "error"
        assert "Unsupported provider" in result["warnings"][0]

    def test_azure_persona_delegates(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "test-role"},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            result = generate_cloud_role("azure", "reader")
            assert result["status"] == "ok"
            mock_gen.assert_called_once_with("azure", "reader", None)

    def test_aws_persona_delegates(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"role_name": "s3-reader"},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            result = generate_cloud_role("aws", "s3-reader")
            assert result["status"] == "ok"
            mock_gen.assert_called_once_with("aws", "s3-reader", None)

    def test_gcp_persona_delegates(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"role_name": "storage-admin"},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            result = generate_cloud_role("gcp", "storage-admin")
            assert result["status"] == "ok"

    def test_resource_types_passed_through(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            generate_cloud_role("aws", "admin", resource_types=["s3", "ec2"])
            mock_gen.assert_called_once_with("aws", "admin", ["s3", "ec2"])

    def test_validation_invalid_marks_warnings(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "broken"},
                "warnings": ["template-warning"],
            }
            mock_val.return_value = {
                "status": "invalid",
                "errors": ["bad-action"],
                "warnings": ["val-warning"],
            }
            result = generate_cloud_role("azure", "writer")
            assert result["status"] == "generated_with_warnings"
            assert "template-warning" in result["warnings"]
            assert any("bad-action" in w for w in result["warnings"])
            assert "val-warning" in result["warnings"]

    def test_generate_role_template_error_propagated(self) -> None:
        with mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen:
            mock_gen.return_value = {
                "status": "error",
                "role_definition": {},
                "warnings": ["template failed"],
            }
            result = generate_cloud_role("aws", "unknown-persona")
            assert result["status"] == "error"
            assert "template failed" in result["warnings"]


class TestValidateCloudRole:
    def test_unsupported_provider(self) -> None:
        result = validate_cloud_role("oracle", {})
        assert result["status"] == "error"
        assert "Unsupported provider" in result["errors"][0]

    def test_azure_valid(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_against_azure_schema",
            return_value=(True, []),
        ):
            result = validate_cloud_role("azure", {"Name": "reader"})
            assert result["status"] == "valid"

    def test_azure_invalid(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_against_azure_schema",
            return_value=(False, ["Missing Actions", "WARNING: broad scope"]),
        ):
            result = validate_cloud_role("azure", {"Name": "bad"})
            assert result["status"] == "invalid"
            assert "Missing Actions" in result["errors"]
            assert "WARNING: broad scope" in result["warnings"]

    def test_aws_valid(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_aws_role",
            return_value={"status": "valid", "errors": [], "warnings": []},
        ):
            result = validate_cloud_role("aws", {"role_name": "s3-reader"})
            assert result["status"] == "valid"

    def test_aws_invalid(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_aws_role",
            return_value={"status": "invalid", "errors": ["bad-arn"], "warnings": ["drift"]},
        ):
            result = validate_cloud_role("aws", {"role_name": "bad"})
            assert result["status"] == "invalid"
            assert "bad-arn" in result["errors"]
            assert "drift" in result["warnings"]

    def test_gcp_valid(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_gcp_role",
            return_value={"status": "valid", "errors": [], "warnings": []},
        ):
            result = validate_cloud_role(
                "gcp", {"role_name": "reader", "bindings": [{"role": "roles/storage.objectViewer"}]}
            )
            assert result["status"] == "valid"

    def test_gcp_invalid(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_gcp_role",
            return_value={"status": "invalid", "errors": ["no bindings"], "warnings": ["deprecated"]},
        ):
            result = validate_cloud_role("gcp", {"role_name": "bad", "bindings": []})
            assert result["status"] == "invalid"


class TestCheckCrossProvider:
    def test_azure_root_scope_warns(self) -> None:
        result = _check_cross_provider("azure", {"AssignableScopes": ["/"]})
        assert any("Write access assigned at root scope" in w for w in result)

    def test_azure_runcommand_without_notactions_warns(self) -> None:
        result = _check_cross_provider("azure", {"Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"]})
        assert any("runCommand" in w for w in result)

    def test_azure_runcommand_with_notactions_no_warn(self) -> None:
        result = _check_cross_provider(
            "azure",
            {
                "Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
                "NotActions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
            },
        )
        assert not any("runCommand" in w for w in result)

    def test_azure_no_root_no_warn(self) -> None:
        result = _check_cross_provider("azure", {"AssignableScopes": ["/subscriptions/sub-id"]})
        assert not any("write access assigned at root scope" in w for w in result)

    def test_aws_passrole_without_condition_warns(self) -> None:
        result = _check_cross_provider(
            "aws",
            {
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["iam:PassRole"],
                    }
                ],
            },
        )
        assert any("PassRole" in w for w in result)

    def test_aws_passrole_with_condition_no_warn(self) -> None:
        result = _check_cross_provider(
            "aws",
            {
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["iam:PassRole"],
                        "Condition": {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
                    }
                ],
            },
        )
        assert not any("PassRole" in w for w in result)

    def test_aws_empty_policy_no_warn(self) -> None:
        result = _check_cross_provider("aws", {"policy": []})
        assert result == []

    def test_aws_non_dict_statement_skipped(self) -> None:
        result = _check_cross_provider("aws", {"policy": ["string", 42]})
        assert result == []

    def test_gcp_owner_binding_warns(self) -> None:
        result = _check_cross_provider("gcp", {"bindings": [{"role": "roles/owner", "members": ["user:test"]}]})
        assert any("owner" in w.lower() for w in result)

    def test_gcp_non_owner_binding_no_warn(self) -> None:
        result = _check_cross_provider("gcp", {"bindings": [{"role": "roles/viewer"}]})
        assert not any("owner" in w.lower() for w in result)


class TestCheckCrossProviderEdgeCases:
    def test_azure_actions_is_string_not_list(self) -> None:
        result = _check_cross_provider("azure", {"Actions": "Microsoft.Compute/virtualMachines/runCommand/action"})
        assert result == []

    def test_azure_assignable_scopes_is_string(self) -> None:
        result = _check_cross_provider("azure", {"AssignableScopes": "/"})
        assert any("Write access assigned at root scope" in w for w in result)

    def test_azure_missing_actions_key(self) -> None:
        result = _check_cross_provider("azure", {"AssignableScopes": ["/subscriptions/sub"]})
        assert result == []

    def test_azure_multiple_runcommand_without_notactions(self) -> None:
        result = _check_cross_provider(
            "azure",
            {
                "Actions": [
                    "Microsoft.Compute/virtualMachines/read",
                    "Microsoft.Compute/virtualMachines/runCommand/action",
                ],
            },
        )
        assert any("runCommand" in w for w in result)
        assert len(result) == 1

    def test_azure_root_scope_and_runcommand(self) -> None:
        result = _check_cross_provider(
            "azure",
            {
                "AssignableScopes": ["/"],
                "Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
            },
        )
        assert len(result) == 2

    def test_azure_runcommand_in_not_actions_as_string(self) -> None:
        result = _check_cross_provider(
            "azure",
            {
                "Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
                "NotActions": "Microsoft.Compute/virtualMachines/runCommand/action",
            },
        )
        assert any("runCommand" in w for w in result)

    def test_azure_empty_dict_no_warn(self) -> None:
        result = _check_cross_provider("azure", {})
        assert result == []

    def test_aws_passrole_effect_deny_no_warn(self) -> None:
        result = _check_cross_provider(
            "aws",
            {"policy": [{"Effect": "Deny", "Action": ["iam:PassRole"]}]},
        )
        assert not any("PassRole" in w for w in result)

    def test_aws_passrole_as_string_not_list(self) -> None:
        result = _check_cross_provider(
            "aws",
            {"policy": [{"Effect": "Allow", "Action": "iam:PassRole"}]},
        )
        assert not any("PassRole" in w for w in result)

    def test_aws_missing_policy_key(self) -> None:
        result = _check_cross_provider("aws", {"role_name": "test"})
        assert result == []

    def test_aws_policy_is_string_not_list(self) -> None:
        result = _check_cross_provider("aws", {"policy": "not-a-list"})
        assert result == []

    def test_aws_multiple_statements_mixed(self) -> None:
        result = _check_cross_provider(
            "aws",
            {
                "policy": [
                    {"Effect": "Deny", "Action": ["iam:PassRole"]},
                    {"Effect": "Allow", "Action": ["iam:PassRole"]},
                    {"Effect": "Allow", "Action": ["s3:GetObject"]},
                ]
            },
        )
        assert any("PassRole" in w for w in result)

    def test_aws_passrole_allow_with_empty_condition(self) -> None:
        result = _check_cross_provider(
            "aws",
            {"policy": [{"Effect": "Allow", "Action": ["iam:PassRole"], "Condition": {}}]},
        )
        assert not any("PassRole" in w for w in result)

    def test_gcp_bindings_is_string(self) -> None:
        result = _check_cross_provider("gcp", {"bindings": "not-a-list"})
        assert result == []

    def test_gcp_binding_not_a_dict(self) -> None:
        result = _check_cross_provider("gcp", {"bindings": ["not-a-dict", {"role": "roles/owner"}]})
        assert any("owner" in w.lower() for w in result)

    def test_gcp_missing_bindings_key(self) -> None:
        result = _check_cross_provider("gcp", {"role_name": "test"})
        assert result == []

    def test_gcp_owner_with_editor_in_same_list(self) -> None:
        result = _check_cross_provider(
            "gcp",
            {
                "bindings": [
                    {"role": "roles/viewer"},
                    {"role": "roles/editor"},
                    {"role": "roles/owner"},
                ]
            },
        )
        assert any("owner" in w.lower() for w in result)

    def test_gcp_owner_empty_members(self) -> None:
        result = _check_cross_provider("gcp", {"bindings": [{"role": "roles/owner", "members": []}]})
        assert any("owner" in w.lower() for w in result)

    def test_unknown_provider_returns_empty(self) -> None:
        result = _check_cross_provider("oracle", {"Actions": ["dangerous"]})
        assert result == []


class TestValidateCloudRoleCrossProviderIntegration:
    def test_azure_valid_schema_with_cross_warnings(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_against_azure_schema",
            return_value=(True, []),
        ):
            result = validate_cloud_role(
                "azure",
                {
                    "Name": "test",
                    "AssignableScopes": ["/"],
                    "Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
                },
            )
            assert result["status"] == "valid"
            assert any("Write access assigned at root scope" in w for w in result["warnings"])
            assert any("runCommand" in w for w in result["warnings"])

    def test_azure_invalid_schema_and_cross_warnings(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_against_azure_schema",
            return_value=(False, ["Missing Actions"]),
        ):
            result = validate_cloud_role(
                "azure",
                {"Name": "test", "AssignableScopes": ["/"]},
            )
            assert result["status"] == "invalid"
            assert "Missing Actions" in result["errors"]
            assert any("Write access assigned at root scope" in w for w in result["warnings"])

    def test_aws_valid_with_cross_warnings(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_aws_role",
            return_value={"status": "valid", "errors": [], "warnings": []},
        ):
            result = validate_cloud_role(
                "aws",
                {"policy": [{"Effect": "Allow", "Action": ["iam:PassRole"]}]},
            )
            assert result["status"] == "valid"
            assert any("PassRole" in w for w in result["warnings"])

    def test_gcp_valid_with_cross_warnings(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_gcp_role",
            return_value={"status": "valid", "errors": [], "warnings": []},
        ):
            result = validate_cloud_role(
                "gcp",
                {"bindings": [{"role": "roles/owner"}]},
            )
            assert result["status"] == "valid"
            assert any("owner" in w.lower() for w in result["warnings"])

    def test_azure_validator_warnings_merged_with_cross(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_against_azure_schema",
            return_value=(False, ["WARNING: deprecated field", "Missing Actions"]),
        ):
            result = validate_cloud_role(
                "azure",
                {"Name": "test", "AssignableScopes": ["/"]},
            )
            assert result["status"] == "invalid"
            assert "Missing Actions" in result["errors"]
            assert any("deprecated field" in w for w in result["warnings"])
            assert any("Write access assigned at root scope" in w for w in result["warnings"])


class TestGenerateCloudRoleDeepIntegration:
    def test_azure_full_pipeline_ok(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "read-only", "Actions": []},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": ["scope-warning"]}
            result = generate_cloud_role("azure", "monitor")
            assert result["status"] == "ok"
            assert "scope-warning" in result["warnings"]

    def test_azure_generated_with_warnings_status(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "test"},
                "warnings": ["pruned-3-actions"],
            }
            mock_val.return_value = {
                "status": "invalid",
                "errors": ["Missing key"],
                "warnings": [],
            }
            result = generate_cloud_role("azure", "monitor")
            assert result["status"] == "generated_with_warnings"
            assert "pruned-3-actions" in result["warnings"]
            assert any("Missing key" in w for w in result["warnings"])

    def test_all_three_providers_generate_via_core(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "ok"},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            for provider in ("azure", "aws", "gcp"):
                result = generate_cloud_role(provider, "monitor")
                assert result["status"] == "ok"

    def test_resource_types_flow_through_entire_pipeline(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "pruned"},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            result = generate_cloud_role("aws", "terraform_deploy", resource_types=["ec2", "s3"])
            mock_gen.assert_called_once_with("aws", "terraform_deploy", ["ec2", "s3"])
            assert result["status"] == "ok"

    def test_empty_resource_types_flow_through(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "full"},
                "warnings": [],
            }
            mock_val.return_value = {"status": "valid", "errors": [], "warnings": []}
            result = generate_cloud_role("azure", "monitor", resource_types=[])
            mock_gen.assert_called_once_with("azure", "monitor", [])
            assert result["status"] == "ok"

    def test_validation_multiple_errors_propagated(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.generate_role_from_template") as mock_gen,
            mock.patch("general_ludd.cloud.core.validate_cloud_role") as mock_val,
        ):
            mock_gen.return_value = {
                "status": "ok",
                "role_definition": {"Name": "broken"},
                "warnings": [],
            }
            mock_val.return_value = {
                "status": "invalid",
                "errors": ["err1", "err2", "err3"],
                "warnings": [],
            }
            result = generate_cloud_role("azure", "monitor")
            assert result["status"] == "generated_with_warnings"
            assert any("3 issue" in w for w in result["warnings"])


class TestValidateCloudRoleProviderDispatch:
    def test_all_three_providers_accepted(self) -> None:
        with (
            mock.patch("general_ludd.cloud.core.validate_against_azure_schema", return_value=(True, [])),
            mock.patch(
                "general_ludd.cloud.core.validate_aws_role",
                return_value={"status": "valid", "errors": [], "warnings": []},
            ),
            mock.patch(
                "general_ludd.cloud.core.validate_gcp_role",
                return_value={"status": "valid", "errors": [], "warnings": []},
            ),
        ):
            for provider in ("azure", "aws", "gcp"):
                result = validate_cloud_role(provider, {"Name": "test"})
                assert result["status"] == "valid"

    def test_gcp_uses_role_name_and_bindings_from_definition(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_gcp_role",
            return_value={"status": "valid", "errors": [], "warnings": []},
        ) as mock_val:
            defn = {"role_name": "my-custom-role", "bindings": [{"role": "roles/viewer"}]}
            validate_cloud_role("gcp", defn)
            mock_val.assert_called_once_with("my-custom-role", [{"role": "roles/viewer"}])

    def test_gcp_empty_role_name_ok(self) -> None:
        with mock.patch(
            "general_ludd.cloud.core.validate_gcp_role",
            return_value={"status": "valid", "errors": [], "warnings": []},
        ) as mock_val:
            validate_cloud_role("gcp", {"bindings": []})
            mock_val.assert_called_once_with("", [])


class TestCrossProviderPatternsAllKeys:
    def test_has_exactly_ten_patterns(self) -> None:
        assert len(CROSS_PROVIDER_PATTERNS) == 10

    def test_setmetadata_allowed_present(self) -> None:
        assert "setmetadata_allowed" in CROSS_PROVIDER_PATTERNS
        assert "metadata-based" in CROSS_PROVIDER_PATTERNS["setmetadata_allowed"]

    def test_fulladmin_managed_policy_present(self) -> None:
        assert "fulladmin_managed_policy" in CROSS_PROVIDER_PATTERNS
        assert "AdministratorAccess" in CROSS_PROVIDER_PATTERNS["fulladmin_managed_policy"]

    def test_no_condition_on_sensitive_present(self) -> None:
        assert "no_condition_on_sensitive" in CROSS_PROVIDER_PATTERNS
        assert "Condition" in CROSS_PROVIDER_PATTERNS["no_condition_on_sensitive"]
