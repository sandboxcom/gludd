#!/usr/bin/env python3
"""validate_azure_iam_policy.py — Validate Azure IAM policy JSON files.

Validates two formats:
  1. azure-iam-policy.json — PascalCase CLI format
     (Name, Description, Actions, NotActions, AssignableScopes, DataActions, NotDataActions)
  2. azure-iam-policy-cli.json — REST API / Portal format
     (wrapped in "properties": roleName, description, permissions[].actions, assignableScopes)

Checks:
  1. All action strings follow a valid Azure RBAC format
  2. No nonexistent suffix patterns (e.g. /list/action where /read is the correct form)
  3. Secret/key/credential operations using /read instead of /action
  4. Actions cross-referenced against PROVIDER_OPERATIONS catalog (warn only)
  5. JSON schema matches what each target (CLI or Portal) expects
  6. AssignableScopes uses the correct subscription placeholder
  7. Required fields present
"""

import json
import re
import sys
from pathlib import Path


INFRA_DIR = Path(__file__).resolve().parent.parent / "config" / "infra"
POLICY_FILE = INFRA_DIR / "azure-iam-policy.json"
POLICY_CLI_FILE = INFRA_DIR / "azure-iam-policy-cli.json"

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

CLI_ACTION_PATTERN = re.compile(
    r"^(\*|[Mm]icrosoft\.\w+)/([\w.*]+/)*(\w+/(read|write|delete|action)|read|write|delete|action|\*)$"
)

FORBIDDEN_SUFFIX_PATTERNS = [
    (r"/list/action$", "/read covers listing; use /read instead of /list/action"),
    (r"/get/action$", "/read covers get; use /read instead of /get/action"),
    (r"/create/action$", "/write covers create; use /write instead of /create/action"),
    (r"/update/action$", "/write covers update; use /write instead of /update/action"),
]

SECRET_ACTION_PATTERNS: dict[str, str] = {
    r"/keys/read$": "Key operations use /action not /read",
    r"/secrets/read$": "Secret operations use /action not /read",
    r"/listCredentials/read$": "Credential listing uses /action not /read",
    r"/listSecrets/read$": "Secret listing uses /action not /read",
}

_RE_SECRET_PATTERNS = {re.compile(pat, re.IGNORECASE): msg for pat, msg in SECRET_ACTION_PATTERNS.items()}

# Lazily import PROVIDER_OPERATIONS from rbac_validator — handle case
# where the module path may not be on sys.path yet.
_ALL_KNOWN_ACTIONS: frozenset[str] | None = None


def _load_known_actions() -> frozenset[str]:
    """Import PROVIDER_OPERATIONS from rbac_validator and flatten into a single set."""
    global _ALL_KNOWN_ACTIONS
    if _ALL_KNOWN_ACTIONS is not None:
        return _ALL_KNOWN_ACTIONS

    try:
        from general_ludd.azure.rbac_validator import all_known_actions  # noqa: PLC0415

        _ALL_KNOWN_ACTIONS = all_known_actions()
    except ImportError:
        _ALL_KNOWN_ACTIONS = frozenset()
    return _ALL_KNOWN_ACTIONS


def validate_action_format(action: str) -> list[str]:
    errors: list[str] = []
    if not CLI_ACTION_PATTERN.match(action):
        errors.append(f"Action '{action}' does not match Azure RBAC format")
    for pattern, explanation in FORBIDDEN_SUFFIX_PATTERNS:
        if re.search(pattern, action, re.IGNORECASE):
            errors.append(f"Action '{action}': {explanation}")
    return errors


def check_secret_action_warnings(action: str) -> list[str]:
    """Check action strings for secret/key/credential operations using /read instead of /action.

    Returns WARNING messages only — these are advisory, not blocking.
    Azure has mixed conventions (e.g. sharedKeys/read is valid for
    OperationalInsights), so this check is intentionally non-blocking.
    """
    warnings: list[str] = []
    for pattern, message in _RE_SECRET_PATTERNS.items():
        if pattern.search(action):
            warnings.append(f"[ADVISORY] Action '{action}' may need /action instead of /read — {message.lower()}")
    return warnings


def validate_cli_format(policy: dict) -> tuple[list[str], list[str], list[str]]:
    """Validate the PascalCase CLI format (azure-iam-policy.json).

    Returns (errors, warnings, all_actions_list).
    """
    errors: list[str] = []
    warnings: list[str] = []

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
    seen: set[str] = set()
    for action in all_actions:
        if action in seen:
            warnings.append(f"Duplicate action: {action}")
        seen.add(action)
        errors.extend(validate_action_format(action))
        warnings.extend(check_secret_action_warnings(action))

    denied = set(policy.get("NotActions", []))
    missing_denials = SECURITY_CRITICAL_DENIED_ACTIONS - denied
    if missing_denials:
        errors.append(f"NotActions missing security-critical denials: {', '.join(sorted(missing_denials))}")

    description = policy.get("Description", "")
    if len(description) < 20:
        warnings.append(f"Description is too short ({len(description)} chars)")

    return errors, warnings, all_actions


def validate_rest_format(policy: dict) -> tuple[list[str], list[str], list[str]]:
    """Validate the REST API / Portal format (azure-iam-policy-cli.json).

    Expected shape: {"properties": {"roleName": ..., "permissions": [{"actions": [...], ...}]}}

    Returns (errors, warnings, all_actions_list).
    """
    errors: list[str] = []
    warnings: list[str] = []

    props = policy.get("properties")
    if not isinstance(props, dict):
        errors.append("Missing or invalid 'properties' object — REST API format requires properties wrapper")
        return errors, warnings, []

    role_name = props.get("roleName")
    if not isinstance(role_name, str) or len(role_name) == 0:
        errors.append("properties.roleName must be a non-empty string")

    description = props.get("description", "")
    if len(description) < 20:
        warnings.append(f"properties.description is too short ({len(description)} chars)")

    scopes = props.get("assignableScopes", [])
    if not isinstance(scopes, list) or len(scopes) == 0:
        errors.append("properties.assignableScopes must be a non-empty list")
    elif not any("{subscription_id}" in s or re.match(r"^/subscriptions/[0-9a-f-]+", s) or s == "/" for s in scopes):
        warnings.append("properties.assignableScopes does not contain subscription-level scope")

    permissions = props.get("permissions")
    if not isinstance(permissions, list) or len(permissions) == 0:
        errors.append("properties.permissions must be a non-empty list")
        return errors, warnings, []

    perm = permissions[0]
    if not isinstance(perm, dict):
        errors.append("properties.permissions[0] must be an object")
        return errors, warnings, []

    actions = perm.get("actions")
    if not isinstance(actions, list) or len(actions) == 0:
        errors.append("properties.permissions[0].actions must be a non-empty list")
        actions = []

    not_actions = perm.get("notActions", [])
    if not isinstance(not_actions, list):
        errors.append("properties.permissions[0].notActions must be a list")
        not_actions = []

    all_actions = actions + not_actions
    seen: set[str] = set()
    for action in all_actions:
        if action in seen:
            warnings.append(f"Duplicate action: {action}")
        seen.add(action)
        errors.extend(validate_action_format(action))
        warnings.extend(check_secret_action_warnings(action))

    denied = set(not_actions)
    missing_denials = SECURITY_CRITICAL_DENIED_ACTIONS - denied
    if missing_denials:
        errors.append(
            f"properties.permissions[0].notActions missing security-critical denials: "
            f"{', '.join(sorted(missing_denials))}"
        )

    return errors, warnings, all_actions


def _check_field_order(policy: dict, label: str) -> list[str]:
    """Check structural field ordering against real-world GitHub reference patterns.

    Returns WARNING messages only — field ordering is advisory.
    """
    warnings: list[str] = []
    fields = list(policy.keys())
    # In most real-world roles, AssignableScopes is the LAST field (after Actions/NotActions)
    assignable_idx = -1
    for i, k in enumerate(fields):
        if k in ("AssignableScopes", "assignableScopes"):
            assignable_idx = i
            break
    if assignable_idx >= 0 and assignable_idx < len(fields) - 1:
        warnings.append(
            f"[{label}] AssignableScopes is field {assignable_idx + 1} of {len(fields)}. "
            f"Real-world GitHub roles place AssignableScopes last (after Actions/NotActions)."
        )
    if assignable_idx == 0:
        warnings.append(f"[{label}] AssignableScopes is the FIRST field. Real-world GitHub roles place it at the END.")
    # IsCustom is auto-added by Azure — warn if present in our source files
    if "IsCustom" in policy or "isCustom" in policy:
        warnings.append(
            f"[{label}] IsCustom field is present — Azure auto-adds this. It is unnecessary in the source file."
        )
    return warnings


def main() -> None:
    errors: list[str] = []
    warnings: list[str] = []

    for policy_file, label, validator in [
        (POLICY_FILE, "CLI (PascalCase)", validate_cli_format),
        (POLICY_CLI_FILE, "REST API / Portal", validate_rest_format),
    ]:
        if not policy_file.exists():
            print(f"MISSING: {policy_file}")
            sys.exit(1)

        try:
            policy = json.loads(policy_file.read_text())
        except json.JSONDecodeError as e:
            print(f"INVALID JSON ({label}): {e}")
            sys.exit(1)

        file_errors, file_warnings, all_actions = validator(policy)

        # Structural warnings (field ordering, IsCustom presence)
        structural_warnings = _check_field_order(policy, label)
        file_warnings.extend(structural_warnings)

        # Prefix errors/warnings with the file label and file name
        for e in file_errors:
            errors.append(f"[{label}] {e}")
        for w in file_warnings:
            warnings.append(f"[{label}] {w}")

        # Cross-reference against known Azure actions (warn only — Azure adds new actions)
        known = _load_known_actions()
        if known:
            for action in all_actions:
                if action not in known and validate_action_format(action) == []:
                    warnings.append(f"[{label}] UNKNOWN: '{action}' not verified against Azure docs")

        action_count = len(all_actions)
        print(f"PASS: {policy_file.name} ({label}) — {action_count} actions parsed")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        for w in warnings:
            print(f"WARN: {w}")
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        sys.exit(1)

    for w in warnings:
        print(f"WARN: {w}")

    print(f"\nPASS: All Azure IAM policy files valid — {len(errors)} errors, {len(warnings)} warnings")
    sys.exit(0)


if __name__ == "__main__":
    main()
