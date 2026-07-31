#!/usr/bin/env python3
"""validate_azure_iam_policy.py — Validate azure-iam-policy.json against known Azure RBAC schema.

Checks:
  1. All action strings follow a valid Azure RBAC format
  2. No nonexistent suffix patterns (e.g. /list/action where /read is the correct form)
  3. JSON schema matches what az role definition create expects
  4. AssignableScopes uses the correct subscription placeholder
  5. Required fields present
"""

import json
import re
import sys
from pathlib import Path


POLICY_FILE = Path(__file__).resolve().parent.parent / "config" / "infra" / "azure-iam-policy.json"

SECURITY_CRITICAL_DENIED_ACTIONS = {
    "Microsoft.Compute/virtualMachines/runCommand/action",
    "Microsoft.Compute/virtualMachines/runCommands/read",
    "Microsoft.Compute/virtualMachines/runCommands/write",
    "Microsoft.Compute/virtualMachines/runCommands/delete",
    "Microsoft.Authorization/roleAssignments/write",
    "Microsoft.Authorization/roleAssignments/delete",
    "Microsoft.Authorization/roleDefinitions/write",
    "Microsoft.Authorization/roleDefinitions/delete",
}

CLI_ACTION_PATTERN = re.compile(r"^[Mm]icrosoft\.\w+/([\w.]+/)*\w+/(read|write|delete|action)$")

FORBIDDEN_SUFFIX_PATTERNS = [
    (r"/list/action$", "/read covers listing; use /read instead of /list/action"),
    (r"/get/action$", "/read covers get; use /read instead of /get/action"),
    (r"/create/action$", "/write covers create; use /write instead of /create/action"),
    (r"/update/action$", "/write covers update; use /write instead of /update/action"),
]


def validate_action_format(action):
    errors = []
    if not CLI_ACTION_PATTERN.match(action):
        errors.append(f"Action '{action}' does not match Azure RBAC format")
    for pattern, explanation in FORBIDDEN_SUFFIX_PATTERNS:
        if re.search(pattern, action, re.IGNORECASE):
            errors.append(f"Action '{action}': {explanation}")
    return errors


def main():
    if not POLICY_FILE.exists():
        print(f"MISSING: {POLICY_FILE}")
        sys.exit(1)

    try:
        policy = json.loads(POLICY_FILE.read_text())
    except json.JSONDecodeError as e:
        print(f"INVALID JSON: {e}")
        sys.exit(1)

    errors = []
    warnings = []

    required = ["Name", "Description", "Actions", "NotActions", "AssignableScopes"]
    missing = [f for f in required if f not in policy]
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")

    if not isinstance(policy.get("Actions"), list) or len(policy["Actions"]) == 0:
        errors.append("Actions must be a non-empty list")

    if not isinstance(policy.get("NotActions"), list):
        errors.append("NotActions must be a list")

    scopes = policy.get("AssignableScopes", [])
    if not isinstance(scopes, list) or len(scopes) == 0:
        errors.append("AssignableScopes must be a non-empty list")
    elif not any("{subscription_id}" in s or re.match(r"^/subscriptions/[0-9a-f-]+", s) or s == "/" for s in scopes):
        warnings.append("AssignableScopes does not contain subscription-level scope")

    all_actions = policy.get("Actions", []) + policy.get("NotActions", [])
    seen = set()
    for action in all_actions:
        if action in seen:
            warnings.append(f"Duplicate action: {action}")
        seen.add(action)
        errors.extend(validate_action_format(action))

    denied = set(policy.get("NotActions", []))
    missing_denials = SECURITY_CRITICAL_DENIED_ACTIONS - denied
    if missing_denials:
        errors.append(f"NotActions missing security-critical denials: {', '.join(sorted(missing_denials))}")

    description = policy.get("Description", "")
    if len(description) < 20:
        warnings.append(f"Description is too short ({len(description)} chars)")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        sys.exit(1)

    if warnings:
        for w in warnings:
            print(f"WARN: {w}")

    print(
        f"PASS: {POLICY_FILE.name} — {len(policy['Actions'])} actions, "
        f"{len(policy['NotActions'])} not-actions, "
        f"{len(policy.get('AssignableScopes', []))} scopes"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
