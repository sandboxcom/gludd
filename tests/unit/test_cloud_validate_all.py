"""Tests for cross-provider IAM validation — validate_all module."""

from __future__ import annotations

from unittest.mock import patch

from general_ludd.cloud.validate_all import main, validate_monitor_roles


class TestValidateMonitorRoles:
    def test_all_providers_valid(self):
        generate_result = {"status": "ok", "role_definition": {"actions": ["read"]}}
        validate_result = {"status": "valid"}

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", return_value=validate_result),
        ):
            rc = validate_monitor_roles()
            assert rc == 0

    def test_generation_warning_still_validates(self):
        generate_result = {"status": "generated_with_warnings", "role_definition": {"actions": ["read"]}}
        validate_result = {"status": "valid"}

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", return_value=validate_result),
        ):
            rc = validate_monitor_roles()
            assert rc == 0

    def test_generation_failure_returns_1(self):
        generate_result = {"status": "error"}

        with patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result):
            rc = validate_monitor_roles()
            assert rc == 1

    def test_validation_failure_returns_1(self):
        generate_result = {"status": "ok", "role_definition": {"actions": ["read"]}}
        validate_result = {"status": "invalid"}

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", return_value=validate_result),
        ):
            rc = validate_monitor_roles()
            assert rc == 1

    def test_missing_role_definition_fails(self):
        generate_result = {"status": "ok"}

        with patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result):
            rc = validate_monitor_roles()
            assert rc == 1

    def test_role_definition_not_dict_fails(self):
        generate_result = {"status": "ok", "role_definition": "not-a-dict"}

        with patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result):
            rc = validate_monitor_roles()
            assert rc == 1


class TestMain:
    def test_main_returns_validate_result(self):
        generate_result = {"status": "ok", "role_definition": {"actions": ["read"]}}
        validate_result = {"status": "valid"}

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", return_value=validate_result),
        ):
            rc = main()
            assert rc == 0

    def test_main_returns_1_on_failure(self):
        generate_result = {"status": "error"}

        with patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=generate_result):
            rc = main()
            assert rc == 1


class TestValidateMixedResults:
    def test_one_provider_validation_fails(self):
        ok_gen = {"status": "ok", "role_definition": {"actions": ["read"]}}
        ok_val = {"status": "valid"}
        inv_val = {"status": "invalid"}

        def mock_generate(provider, persona):
            return ok_gen

        def mock_validate(provider, role_def):
            if provider == "aws":
                return inv_val
            return ok_val

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", side_effect=mock_generate),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", side_effect=mock_validate),
        ):
            rc = validate_monitor_roles()
            assert rc == 1

    def test_mixed_gen_status_one_error(self):
        def mock_generate(provider, persona):
            if provider == "gcp":
                return {"status": "error"}
            return {"status": "ok", "role_definition": {"actions": ["read"]}}

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", side_effect=mock_generate),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", return_value={"status": "valid"}),
        ):
            rc = validate_monitor_roles()
            assert rc == 1

    def test_generated_with_warnings_all_pass(self):
        gen = {"status": "generated_with_warnings", "role_definition": {"actions": ["read"]}}
        val = {"status": "valid"}

        with (
            patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=gen),
            patch("general_ludd.cloud.validate_all.validate_cloud_role", return_value=val),
        ):
            rc = validate_monitor_roles()
            assert rc == 0

    def test_unknown_generation_status_fails(self):
        gen = {"status": "unknown_status", "role_definition": {"actions": ["read"]}}

        with patch("general_ludd.cloud.validate_all.generate_cloud_role", return_value=gen):
            rc = validate_monitor_roles()
            assert rc == 1
