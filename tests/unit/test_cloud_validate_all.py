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
