#!/usr/bin/env python3
"""validate_azure_iam_policy.py — Validate azure-iam-policy.json against known Azure RBAC schema.

Checks:
  1. All action strings follow a valid Azure RBAC format
  2. No nonexistent suffix patterns (e.g. /list/action where /read is the correct form)
  3. Secret/key/credential operations using /read instead of /action
  4. Actions cross-referenced against PROVIDER_OPERATIONS catalog (warn only)
  5. JSON schema matches what az role definition create expects
  6. AssignableScopes uses the correct subscription placeholder
  7. Required fields present
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

SECRET_ACTION_PATTERNS: dict[str, str] = {
    r"/keys/read$": "Key operations use /action not /read",
    r"/secrets/read$": "Secret operations use /action not /read",
    r"/sharedKeys/read$": "Shared key operations use /action not /read",
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
    for pattern, message in _RE_SECRET_PATTERNS.items():
        if pattern.search(action):
            errors.append(f"Action '{action}' may need /action instead of /read — {message.lower()}")
    return errors


def main() -> None:
    if not POLICY_FILE.exists():
        print(f"MISSING: {POLICY_FILE}")
        sys.exit(1)

    try:
        policy = json.loads(POLICY_FILE.read_text())
    except json.JSONDecodeError as e:
        print(f"INVALID JSON: {e}")
        sys.exit(1)

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

    denied = set(policy.get("NotActions", []))
    missing_denials = SECURITY_CRITICAL_DENIED_ACTIONS - denied
    if missing_denials:
        errors.append(f"NotActions missing security-critical denials: {', '.join(sorted(missing_denials))}")

    description = policy.get("Description", "")
    if len(description) < 20:
        warnings.append(f"Description is too short ({len(description)} chars)")

    # Cross-reference against known Azure actions (warn only — Azure adds new actions)
    known = _load_known_actions()
    if known:
        for action in all_actions:
            if action not in known and validate_action_format(action) == []:
                warnings.append(f"UNKNOWN: '{action}' not verified against Azure docs")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        for w in warnings:
            print(f"WARN: {w}")
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        sys.exit(1)

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
