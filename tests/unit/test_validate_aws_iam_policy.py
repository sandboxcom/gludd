"""Unit tests for scripts/validate_aws_iam_policy.py.

Covers the validator's checks on aws-iam-roles.yml and edge cases for
malformed inputs so the validator itself is tested structurally.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
POLICY_FILE = REPO_ROOT / "config" / "infra" / "aws-iam-roles.yml"

sys.path.insert(0, str(SCRIPTS_DIR))


def _import_validator():
    import importlib

    import validate_aws_iam_policy as mod  # type: ignore[import-untyped]

    importlib.reload(mod)
    return mod


def _real_doc() -> dict[str, Any]:
    return yaml.safe_load(POLICY_FILE.read_text())


def _minimal_valid_doc() -> dict[str, Any]:
    return {
        "roles": {
            "terraform_deploy": {
                "description": "Provision gludd infrastructure via Terraform",
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["ec2:Describe*", "ec2:RunInstances", "iam:PassRole"],
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "ec2:InstanceType": ["t3.medium"],
                            }
                        },
                    }
                ],
            },
            "runtime_execution": {
                "description": "Run gludd daemon at runtime on EC2",
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["ec2:DescribeInstances", "s3:GetObject"],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Deny",
                        "Action": ["iam:*"],
                        "Resource": "*",
                    },
                ],
            },
            "model_inference": {
                "description": "Call AI model APIs (SageMaker, Bedrock)",
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["sagemaker:InvokeEndpoint"],
                        "Resource": "*",
                    }
                ],
            },
            "monitor": {
                "description": "Read-only monitoring dashboards",
                "policy": [
                    {
                        "Effect": "Allow",
                        "Action": ["cloudwatch:GetMetricData"],
                        "Resource": "*",
                    }
                ],
            },
        }
    }


# ---------------------------------------------------------------------------
# Real-file tests (run against aws-iam-roles.yml)
# ---------------------------------------------------------------------------


class TestRealAwsIamPolicy:
    def test_yaml_parses(self) -> None:
        mod = _import_validator()
        doc = mod.validate_yaml_parse()
        assert isinstance(doc, dict)
        assert "roles" in doc

    def test_all_four_personas_present(self) -> None:
        mod = _import_validator()
        doc = _real_doc()
        actual = frozenset(doc["roles"].keys())
        assert actual == mod.REQUIRED_PERSONAS

    def test_validate_passes(self) -> None:
        mod = _import_validator()
        errors = mod.validate(_real_doc())
        assert errors == [], f"Real policy has errors: {errors}"

    def test_no_admin_wildcard(self) -> None:
        mod = _import_validator()
        doc = _real_doc()
        for role_name, role_def in doc["roles"].items():
            for stmt in role_def["policy"]:
                actions = mod.collect_actions(stmt)
                for action in actions:
                    assert action != "*:*", f"Role '{role_name}' has *:*"
                    if stmt.get("Effect") == "Allow":
                        assert action != "*", f"Role '{role_name}' Allow has bare '*'"

    def test_runtime_has_deny_block(self) -> None:
        doc = _real_doc()
        rt = doc["roles"]["runtime_execution"]
        has_deny = any(s.get("Effect") == "Deny" for s in rt["policy"])
        assert has_deny, "runtime_execution must have a Deny block"

    def test_passrole_has_condition(self) -> None:
        mod = _import_validator()
        doc = _real_doc()
        for role_name, role_def in doc["roles"].items():
            for stmt in role_def["policy"]:
                if stmt.get("Effect") != "Allow":
                    continue
                actions = mod.collect_actions(stmt)
                if "iam:PassRole" in actions:
                    assert "Condition" in stmt, f"Role '{role_name}': iam:PassRole lacks Condition"

    def test_no_administrator_access(self) -> None:
        mod = _import_validator()
        doc = _real_doc()
        for role_name, role_def in doc["roles"].items():
            for stmt in role_def["policy"]:
                actions = mod.collect_actions(stmt)
                for action in actions:
                    assert "AdministratorAccess" not in action, f"Role '{role_name}' references AdministratorAccess"
                    assert "PowerUserAccess" not in action, f"Role '{role_name}' references PowerUserAccess"

    def test_terraform_deploy_has_ec2_permissions(self) -> None:
        mod = _import_validator()
        doc = _real_doc()
        tf = doc["roles"]["terraform_deploy"]
        all_actions: list[str] = []
        for stmt in tf["policy"]:
            all_actions.extend(mod.collect_actions(stmt))
        ec2_actions = [a for a in all_actions if a.startswith("ec2:")]
        assert len(ec2_actions) >= 3, "terraform_deploy should have at least 3 EC2 permissions"
        assert any("RunInstances" in a for a in ec2_actions), "terraform_deploy must have ec2:RunInstances"
        assert any("Describe" in a for a in ec2_actions), "terraform_deploy must have ec2:Describe*"

    def test_runinstances_has_instance_type_condition(self) -> None:
        mod = _import_validator()
        doc = _real_doc()
        tf = doc["roles"]["terraform_deploy"]
        for stmt in tf["policy"]:
            if stmt.get("Effect") != "Allow":
                continue
            actions = mod.collect_actions(stmt)
            if "ec2:RunInstances" in actions:
                cond = stmt.get("Condition", {})
                assert isinstance(cond, dict) and len(cond) > 0
                found = False
                for _cop, cmap in cond.items():
                    if isinstance(cmap, dict):
                        for k in cmap:
                            if "InstanceType" in k:
                                found = True
                assert found, "RunInstances Condition must include InstanceType restriction"


# ---------------------------------------------------------------------------
# Synthetic-input tests (edge cases for the validator logic)
# ---------------------------------------------------------------------------


class TestSyntheticEdgeCases:
    def test_missing_persona_detected(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        del doc["roles"]["monitor"]
        errors = mod.validate(doc)
        assert any("monitor" in e for e in errors)

    def test_star_star_detected(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        doc["roles"]["monitor"]["policy"][0]["Action"] = ["*:*"]
        errors = mod.validate(doc)
        assert any("*:*" in e for e in errors)

    def test_bare_star_in_allow_detected(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        doc["roles"]["monitor"]["policy"][0]["Action"] = ["*"]
        errors = mod.validate(doc)
        assert any("bare '*'" in e for e in errors)

    def test_passrole_without_condition(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        tf_policy = doc["roles"]["terraform_deploy"]["policy"][0]
        del tf_policy["Condition"]
        errors = mod.validate(doc)
        assert any("iam:PassRole" in e and "no Condition" in e for e in errors)

    def test_runtime_missing_deny(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        rt_policy = doc["roles"]["runtime_execution"]["policy"]
        doc["roles"]["runtime_execution"]["policy"] = [p for p in rt_policy if p.get("Effect") != "Deny"]
        errors = mod.validate(doc)
        assert any("runtime_execution" in e and "no Deny" in e for e in errors)

    def test_runinstances_no_restriction(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        tf = doc["roles"]["terraform_deploy"]["policy"][0]
        del tf["Condition"]
        errors = mod.validate(doc)
        assert any("ec2:RunInstances" in e and "no Condition" in e for e in errors)

    def test_administrator_access_detected(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        doc["roles"]["monitor"]["policy"][0]["Action"] = ["AdministratorAccess"]
        errors = mod.validate(doc)
        assert any("AdministratorAccess" in e for e in errors)

    def test_empty_policy_list(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        doc["roles"]["monitor"]["policy"] = []
        errors = mod.validate(doc)
        assert any("empty" in e for e in errors)

    def test_short_description(self) -> None:
        mod = _import_validator()
        doc = _minimal_valid_doc()
        doc["roles"]["monitor"]["description"] = "too short"
        errors = mod.validate(doc)
        assert any("'monitor'" in e and "too short" in e for e in errors)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_required_personas_has_four(self) -> None:
        mod = _import_validator()
        assert len(mod.REQUIRED_PERSONAS) == 4
        assert "terraform_deploy" in mod.REQUIRED_PERSONAS
        assert "runtime_execution" in mod.REQUIRED_PERSONAS
        assert "model_inference" in mod.REQUIRED_PERSONAS
        assert "monitor" in mod.REQUIRED_PERSONAS

    def test_min_desc_length(self) -> None:
        mod = _import_validator()
        assert mod.MIN_DESC_LENGTH >= 10
