"""Tests for src/general_ludd/cloud/aws_validator.py."""

from __future__ import annotations

from general_ludd.cloud.aws_validator import (
    AWS_REQUIRED_DENIALS,
    _collect_actions,
    validate_aws_role,
)


class TestCollectActions:
    def test_string_action(self) -> None:
        assert _collect_actions({"Action": "s3:GetObject"}) == ["s3:GetObject"]

    def test_list_actions(self) -> None:
        assert _collect_actions({"Action": ["s3:GetObject", "s3:PutObject"]}) == [
            "s3:GetObject",
            "s3:PutObject",
        ]

    def test_missing_action(self) -> None:
        assert _collect_actions({}) == []

    def test_non_string_non_list_action(self) -> None:
        assert _collect_actions({"Action": 123}) == []


class TestValidateAwsRole:
    def test_policy_not_a_list(self) -> None:
        result = validate_aws_role({"policy": "not-a-list"})
        assert result["status"] == "invalid"
        assert "policy must be a list" in result["errors"]

    def test_description_too_short(self) -> None:
        result = validate_aws_role({"policy": [], "role_name": "test", "description": "short"})
        assert result["status"] == "invalid"
        assert any("Description too short" in e for e in result["errors"])

    def test_description_ok(self) -> None:
        result = validate_aws_role(
            {
                "policy": [],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert all("Description" not in e for e in result["errors"])

    def test_star_star_admin_wildcard_forbidden(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "*:*", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert "*:* admin wildcard forbidden" in result["errors"][0]

    def test_bare_star_in_allow_forbidden(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "*", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert "bare '*' in Allow block forbidden" in result["errors"][0]

    def test_bare_star_in_deny_allowed(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Deny", "Action": "*", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert not any("bare '*'" in e for e in result["errors"])

    def test_passrole_allow_without_condition(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "iam:PassRole", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("iam:PassRole Allow without Condition" in e for e in result["errors"])

    def test_passrole_allow_with_condition(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": "iam:PassRole",
                        "Resource": "*",
                        "Condition": {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
                    }
                ],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("iam:PassRole has Condition" in w for w in result["warnings"])
        assert not any("PassRole" in e for e in result["errors"])

    def test_ec2_runinstances_allow_without_condition(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "ec2:RunInstances", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("ec2:RunInstances Allow without Condition" in e for e in result["errors"])

    def test_ec2_runinstances_allow_with_condition_missing_instance_type(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": "ec2:RunInstances",
                        "Resource": "*",
                        "Condition": {"StringEquals": {"aws:RequestedRegion": "us-east-1"}},
                    }
                ],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("missing instance-type restriction" in e for e in result["errors"])

    def test_ec2_runinstances_allow_with_instance_type_condition(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": "ec2:RunInstances",
                        "Resource": "*",
                        "Condition": {"StringEquals": {"ec2:InstanceType": "t3.micro"}},
                    }
                ],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert not any("ec2:RunInstances" in e for e in result["errors"])

    def test_full_admin_managed_policy_forbidden(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "AdministratorAccess", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("AdministratorAccess" in e for e in result["errors"])

    def test_poweruser_managed_policy_forbidden(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "PowerUserAccess", "Resource": "*"}],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("PowerUserAccess" in e for e in result["errors"])

    def test_runtime_execution_must_have_deny(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"}],
                "role_name": "runtime_execution",
                "description": "a" * 20,
            }
        )
        assert any("runtime_execution persona must include a Deny" in e for e in result["errors"])

    def test_runtime_execution_with_deny_ok(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"},
                    {"Effect": "Deny", "Action": "iam:CreateUser", "Resource": "*"},
                ],
                "role_name": "runtime_execution",
                "description": "a" * 20,
            }
        )
        assert not any("must include a Deny" in e for e in result["errors"])

    def test_missing_required_denials_warning(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {"Effect": "Allow", "Action": "sagemaker:*", "Resource": "*"},
                    {"Effect": "Deny", "Action": "ec2:RunInstances", "Resource": "*"},
                ],
                "role_name": "model_inference",
                "description": "a" * 20,
            }
        )
        assert any("Recommended denials not present" in w for w in result["warnings"])

    def test_all_required_denials_present_no_warning(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {"Effect": "Allow", "Action": "sagemaker:*", "Resource": "*"},
                    {
                        "Effect": "Deny",
                        "Action": ["iam:PassRole", "ec2:RunInstances", "ec2:CreateVolume"],
                        "Resource": "*",
                    },
                ],
                "role_name": "model_inference",
                "description": "a" * 20,
            }
        )
        assert not any("Recommended denials" in w for w in result["warnings"])

    def test_valid_role_no_errors(self) -> None:
        result = validate_aws_role(
            {
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:ListBucket"],
                        "Resource": ["arn:aws:s3:::my-bucket", "arn:aws:s3:::my-bucket/*"],
                    },
                ],
                "role_name": "monitor",
                "description": "Monitoring role for production S3 bucket access",
            }
        )
        assert result["status"] == "valid"
        assert len(result["errors"]) == 0

    def test_statement_not_a_dict(self) -> None:
        result = validate_aws_role(
            {
                "policy": ["not-a-dict"],
                "role_name": "test",
                "description": "a" * 20,
            }
        )
        assert any("is not a dict" in e for e in result["errors"])

    def test_terraform_deploy_no_required_denials(self) -> None:
        result = validate_aws_role(
            {
                "policy": [{"Effect": "Allow", "Action": "*", "Resource": "*"}],
                "role_name": "terraform_deploy",
                "description": "a" * 20,
            }
        )
        assert not any("Recommended denials" in w for w in result["warnings"])


class TestAwsRequiredDenials:
    def test_required_denials_keys(self) -> None:
        assert set(AWS_REQUIRED_DENIALS.keys()) == {
            "runtime_execution",
            "terraform_deploy",
            "model_inference",
            "monitor",
        }

    def test_terraform_deploy_empty(self) -> None:
        assert AWS_REQUIRED_DENIALS["terraform_deploy"] == []

    def test_runtime_execution_denials_non_empty(self) -> None:
        assert len(AWS_REQUIRED_DENIALS["runtime_execution"]) > 0
        assert "iam:CreateUser" in AWS_REQUIRED_DENIALS["runtime_execution"]
