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
