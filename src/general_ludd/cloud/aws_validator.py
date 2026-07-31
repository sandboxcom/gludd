"""AWS IAM validator — policy-statement checks, PassRole scoping, Deny-block
enforcement, and persona-level required denials.

Derived from the validation logic in ``scripts/validate_aws_iam_policy.py``.
"""

from __future__ import annotations

from typing import Any

AWS_REQUIRED_DENIALS: dict[str, list[str]] = {
    "runtime_execution": [
        "iam:CreateUser",
        "iam:CreateAccessKey",
        "iam:PutUserPolicy",
        "iam:AttachUserPolicy",
        "iam:AddUserToGroup",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:CreateSecurityGroup",
    ],
    "terraform_deploy": [],
    "model_inference": [
        "iam:PassRole",
        "ec2:RunInstances",
        "ec2:CreateVolume",
    ],
    "monitor": [
        "iam:CreateUser",
        "iam:PassRole",
    ],
}

_ELEVATION_ACTIONS: frozenset[str] = frozenset(
    {
        "iam:CreateUser",
        "iam:CreateAccessKey",
        "iam:PutUserPolicy",
        "iam:AttachUserPolicy",
        "iam:AddUserToGroup",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:CreateSecurityGroup",
        "ec2:RunInstances",
        "ec2:CreateVolume",
    }
)


def validate_aws_role(role_json: dict[str, Any]) -> dict[str, Any]:
    """Validate an AWS IAM role definition for least-privilege compliance.

    Checks:
    - No ``*:*`` admin wildcard
    - No bare ``*`` in Allow blocks
    - ``iam:PassRole`` in Allow blocks must have a Condition
    - ``ec2:RunInstances`` must restrict instance types
    - ``runtime_execution`` persona must have a Deny block
    - Managed policies: no AdministratorAccess or PowerUserAccess inline
    """
    errors: list[str] = []
    warnings: list[str] = []

    policy = role_json.get("policy", [])
    if not isinstance(policy, list):
        return {"status": "invalid", "errors": ["policy must be a list"], "warnings": warnings}

    persona = role_json.get("role_name", "")
    description = role_json.get("description", "")
    if len(description) < 20:
        errors.append(f"Description too short ({len(description)} chars, minimum 20)")

    has_deny = False

    for stmt_idx, stmt in enumerate(policy):
        if not isinstance(stmt, dict):
            errors.append(f"Statement [{stmt_idx}] is not a dict")
            continue

        effect = stmt.get("Effect", "")
        actions = _collect_actions(stmt)
        label = f"stmt [{stmt_idx}]"

        for action in actions:
            if action == "*:*":
                errors.append(f"{label}: *:* admin wildcard forbidden")
            if action == "*" and effect != "Deny":
                errors.append(f"{label}: bare '*' in Allow block forbidden")
            if "AdministratorAccess" in action or "PowerUserAccess" in action:
                errors.append(f"{label}: full-admin managed policy {action} forbidden")

        if effect == "Deny":
            has_deny = True

        if "iam:PassRole" in actions and effect == "Allow":
            if "Condition" not in stmt:
                errors.append(f"{label}: iam:PassRole Allow without Condition — must scope to specific role ARNs")
            else:
                warnings.append(f"{label}: iam:PassRole has Condition; verify scope matches intended roles")

        if "ec2:RunInstances" in actions and effect == "Allow":
            cond = stmt.get("Condition", {})
            if not isinstance(cond, dict) or len(cond) == 0:
                errors.append(f"{label}: ec2:RunInstances Allow without Condition — must restrict instance types")
            else:
                found = any(
                    "InstanceType" in key
                    for cond_map in cond.values()
                    if isinstance(cond_map, dict)
                    for key in cond_map
                )
                if not found:
                    errors.append(f"{label}: ec2:RunInstances Condition missing instance-type restriction")

    if persona == "runtime_execution" and not has_deny:
        errors.append("runtime_execution persona must include a Deny block for privilege-escalation actions")

    if persona in AWS_REQUIRED_DENIALS:
        required = AWS_REQUIRED_DENIALS[persona]
        if required:
            all_denied: set[str] = set()
            for stmt in policy:
                if isinstance(stmt, dict) and stmt.get("Effect") == "Deny":
                    all_denied.update(_collect_actions(stmt))
            missing_denials = [a for a in required if a not in all_denied]
            if missing_denials:
                warnings.append(f"Recommended denials not present: {missing_denials}")

    status = "valid" if not errors else "invalid"
    return {"status": status, "errors": errors, "warnings": warnings}


def _collect_actions(statement: dict[str, Any]) -> list[str]:
    raw = statement.get("Action", [])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return raw
    return []


__all__ = [
    "AWS_REQUIRED_DENIALS",
    "validate_aws_role",
]
