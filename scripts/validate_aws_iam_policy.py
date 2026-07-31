#!/usr/bin/env python3
"""validate_aws_iam_policy.py — Validate AWS IAM role policy definitions.

Validates config/infra/aws-iam-roles.yml against least-privilege rules.

Checks:
  1. YAML file parses correctly
  2. All 4 personas (terraform_deploy, runtime_execution, model_inference, monitor) present
  3. Every persona has a non-empty policy list
  4. No policy statement uses *:* (admin wildcard)
  5. Every iam:PassRole statement has a Condition block
  6. runtime_execution has a Deny block (no privilege escalation path)
  7. No AdministratorAccess or PowerUserAccess inline
  8. ec2:RunInstances has instance type conditions
  9. Every persona has a description
"""

import sys
from pathlib import Path

import yaml


INFRA_DIR = Path(__file__).resolve().parent.parent / "config" / "infra"
POLICY_FILE = INFRA_DIR / "aws-iam-roles.yml"

REQUIRED_PERSONAS = frozenset(
    {
        "terraform_deploy",
        "runtime_execution",
        "model_inference",
        "monitor",
    }
)

MIN_DESC_LENGTH = 20


def validate_yaml_parse() -> dict:
    if not POLICY_FILE.exists():
        print(f"MISSING: {POLICY_FILE}")
        sys.exit(1)
    try:
        doc = yaml.safe_load(POLICY_FILE.read_text())
    except yaml.YAMLError as e:
        print(f"INVALID YAML: {e}")
        sys.exit(1)
    if not isinstance(doc, dict):
        print(f"FAIL: root element is not a dict (got {type(doc).__name__})")
        sys.exit(1)
    if "roles" not in doc:
        print("FAIL: root document missing 'roles' key")
        sys.exit(1)
    print(f"PASS: YAML parse — {POLICY_FILE.name}")
    return doc


def collect_actions(statement: dict) -> list[str]:
    raw = statement.get("Action", [])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return raw
    return []


def validate(doc: dict) -> list[str]:
    errors: list[str] = []
    roles = doc["roles"]

    # 2. All required personas present
    actual = frozenset(roles.keys())
    missing = REQUIRED_PERSONAS - actual
    if missing:
        errors.append(f"Missing personas: {', '.join(sorted(missing))}")

    for role_name, role_def in roles.items():
        if not isinstance(role_def, dict):
            errors.append(f"Role '{role_name}' definition is not a dict")
            continue

        # 3. Policy list present and non-empty
        policy = role_def.get("policy", [])
        if not isinstance(policy, list) or len(policy) == 0:
            errors.append(f"Role '{role_name}' has empty or missing policy list")
            continue

        # Description check
        desc = role_def.get("description", "")
        if len(desc) < MIN_DESC_LENGTH:
            errors.append(f"Role '{role_name}' description too short ({len(desc)} chars, need >={MIN_DESC_LENGTH})")

        has_deny = False

        for stmt_idx, stmt in enumerate(policy):
            if not isinstance(stmt, dict):
                errors.append(f"Role '{role_name}' statement [{stmt_idx}] is not a dict")
                continue

            effect = stmt.get("Effect", "")
            actions = collect_actions(stmt)
            stmt_label = f"Role '{role_name}' stmt [{stmt_idx}]"

            # 4. No *:* admin wildcard
            for action in actions:
                if action == "*:*":
                    errors.append(f"{stmt_label}: contains *:* admin wildcard — forbidden")
                if action == "*" and effect != "Deny":
                    errors.append(f"{stmt_label}: bare '*' action in Allow block — forbidden")

            # 8. AdministratorAccess / PowerUserAccess check
            for action in actions:
                if "AdministratorAccess" in action or "PowerUserAccess" in action:
                    errors.append(f"{stmt_label}: references {action} — full-admin managed policy forbidden")

            if effect == "Deny":
                has_deny = True

            # 5. iam:PassRole must have Condition
            if "iam:PassRole" in actions and effect == "Allow":
                if "Condition" not in stmt:
                    errors.append(
                        f"{stmt_label}: iam:PassRole in Allow block has no Condition — must scope to specific role ARNs"
                    )

            # 9. ec2:RunInstances must have instance type condition
            if "ec2:RunInstances" in actions and effect == "Allow":
                cond = stmt.get("Condition", {})
                if not isinstance(cond, dict) or len(cond) == 0:
                    errors.append(
                        f"{stmt_label}: ec2:RunInstances in Allow block has no Condition — must restrict instance types"
                    )
                else:
                    found_itype = False
                    for _cond_op, cond_map in cond.items():
                        if isinstance(cond_map, dict):
                            for key in cond_map:
                                if "InstanceType" in key:
                                    found_itype = True
                                    break
                    if not found_itype:
                        errors.append(
                            f"{stmt_label}: ec2:RunInstances Condition does not include an instance-type restriction"
                        )

        # 6. runtime_execution must have a Deny block
        if role_name == "runtime_execution" and not has_deny:
            errors.append(
                "Role 'runtime_execution' has no Deny block — must deny "
                "privilege-escalation actions (iam:*, security-group mutations)"
            )

        print(f"PASS: Role '{role_name}' — {len(policy)} statement(s)")

    return errors


def main() -> None:
    doc = validate_yaml_parse()
    errors = validate(doc)

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print(f"\n{len(errors)} error(s)")
        sys.exit(1)

    print(f"\nPASS: All AWS IAM role policies valid — 0 errors")
    sys.exit(0)


if __name__ == "__main__":
    main()
