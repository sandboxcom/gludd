#!/usr/bin/env python3
"""crossref_azure_actions.py — Cross-reference our Azure IAM policy actions against
Microsoft's published custom-role examples AND real-world GitHub reference roles.

Loads:
  - config/infra/azure-iam-policy.json
  - config/infra/azure-iam-policy-cli.json
  - config/infra/azure-ms-reference-roles.json (Microsoft docs examples)
  - config/infra/azure-github-reference-roles.json (real-world GitHub roles)

For every action in our policy:
  1. Checks whether its provider namespace appears in any reference role.
  2. Checks whether every action follows the same structural pattern as reference actions.
  3. Prints VERIFIED/UNVERIFIED per action with the reference source.

Returns exit 0 when all providers are verified; exit 1 when unverified providers exist.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INFRA_DIR = REPO_ROOT / "config" / "infra"

OUR_POLICIES = [
    INFRA_DIR / "azure-iam-policy.json",
    INFRA_DIR / "azure-iam-policy-cli.json",
]

MS_REFERENCE = INFRA_DIR / "azure-ms-reference-roles.json"
GH_REFERENCE = INFRA_DIR / "azure-github-reference-roles.json"

_PROVIDER_RE = re.compile(r"^(Microsoft\.\w+)/")
_ACTION_STRUCT_RE = re.compile(
    r"^([Mm]icrosoft\.\w+/([\w.*]+/)*\w+/(read|write|delete|action)"
    r"|[Mm]icrosoft\.\w+/([\w.*]+/)*\*"
    r"|\*/(read|write|delete|action|\*))$"
)


def _extract_provider(action: str) -> str | None:
    m = _PROVIDER_RE.match(action)
    return m.group(1) if m else None


def _actions_from_example(example: dict) -> list[str]:
    """Extract action strings from a REST-API-format example (properties.permissions wrapper)."""
    actions: list[str] = []
    props = example.get("properties", {})
    for perm in props.get("permissions", []):
        actions.extend(perm.get("actions", []))
        actions.extend(perm.get("notActions", []))
        actions.extend(perm.get("dataActions", []))
        actions.extend(perm.get("notDataActions", []))
    return actions


def _actions_from_pascal_case(role: dict) -> list[str]:
    """Extract action strings from a PascalCase format role (GitHub ref style)."""
    actions: list[str] = []
    actions.extend(role.get("Actions", []))
    actions.extend(role.get("NotActions", []))
    actions.extend(role.get("DataActions", []))
    actions.extend(role.get("NotDataActions", []))
    return actions


def _providers_from_role(actions: list[str]) -> set[str]:
    providers: set[str] = set()
    for action in actions:
        provider = _extract_provider(action)
        if provider:
            providers.add(provider)
    return providers


def _collect_ref_actions(roles: list[dict], source_label: str) -> tuple[set[str], set[str]]:
    """Collect provider namespaces and action strings from a list of reference roles.

    Returns (providers, actions).
    """
    all_providers: set[str] = set()
    all_actions: set[str] = set()
    for role in roles:
        if "properties" in role:
            actions = _actions_from_example(role)
        else:
            actions = _actions_from_pascal_case(role)
        all_providers |= _providers_from_role(actions)
        all_actions.update(actions)
    return all_providers, all_actions


def _actions_from_policy(policy: dict) -> list[str]:
    """Extract action strings from our policy JSON (either PascalCase or REST format)."""
    actions: list[str] = []
    if "Actions" in policy:
        actions.extend(policy.get("Actions", []))
        actions.extend(policy.get("NotActions", []))
    elif "properties" in policy:
        for perm in policy["properties"].get("permissions", []):
            actions.extend(perm.get("actions", []))
            actions.extend(perm.get("notActions", []))
    return actions


def _check_action_structural_pattern(action: str) -> bool:
    """Return True if the action follows the Azure RBAC structural pattern."""
    # Wildcard actions like "*/read" or "Microsoft.Compute/*" are valid
    if action.startswith("*/") or action.endswith("/*"):
        return True
    return bool(_ACTION_STRUCT_RE.match(action))


def _find_closest_action(action: str, ref_actions: set[str]) -> str | None:
    """Find the closest matching action in the reference set by prefix match."""
    provider = _extract_provider(action)
    if not provider:
        return None
    for ra in sorted(ref_actions):
        if ra.startswith(provider + "/"):
            return ra
    return None


def _role_field_order(role: dict) -> list[str]:
    """Return the order of standard Azure RBAC fields in a role dict."""
    field_order = [
        "Name",
        "IsCustom",
        "Id",
        "Description",
        "Actions",
        "NotActions",
        "DataActions",
        "NotDataActions",
        "AssignableScopes",
    ]
    return [k for k in field_order if k in role]


def main() -> None:
    # --- Load all reference data ---
    all_ref_providers: set[str] = set()
    all_ref_actions: set[str] = set()
    ref_sources: dict[str, set[str]] = {}  # provider -> {source1, source2}

    # 1. MS reference roles
    if MS_REFERENCE.exists():
        try:
            ms_data = json.loads(MS_REFERENCE.read_text())
        except json.JSONDecodeError as e:
            print(f"INVALID JSON in {MS_REFERENCE}: {e}")
            sys.exit(1)
        ms_examples = ms_data.get("examples", [])
        ms_providers, ms_actions = _collect_ref_actions(ms_examples, "MS")
        for p in ms_providers:
            ref_sources.setdefault(p, set()).add("MS")
        all_ref_providers |= ms_providers
        all_ref_actions.update(ms_actions)
        print(f"MS ref providers ({len(ms_providers)}): {', '.join(sorted(ms_providers))}")
    else:
        print(f"MISSING (warn): {MS_REFERENCE}")

    # 2. GitHub reference roles
    gh_roles: list[dict] = []
    if GH_REFERENCE.exists():
        try:
            gh_roles = json.loads(GH_REFERENCE.read_text())
        except json.JSONDecodeError as e:
            print(f"INVALID JSON in {GH_REFERENCE}: {e}")
            sys.exit(1)
        gh_providers, gh_actions = _collect_ref_actions(gh_roles, "GitHub")
        for p in gh_providers:
            ref_sources.setdefault(p, set()).add("GitHub")
        all_ref_providers |= gh_providers
        all_ref_actions.update(gh_actions)
        print(f"GitHub ref providers ({len(gh_providers)}): {', '.join(sorted(gh_providers))}")
    else:
        print(f"MISSING (warn): {GH_REFERENCE}")

    if not all_ref_providers:
        print("FAIL: No reference providers loaded from any source")
        sys.exit(1)

    print(f"\nCombined ref providers: {len(all_ref_providers)}")
    print(f"Combined ref actions: {len(all_ref_actions)}")

    # --- Structural analysis of GitHub ref roles ---
    if GH_REFERENCE.exists():
        print("\n--- GitHub Reference Role Structural Analysis ---")
        for role in gh_roles:
            name = role.get("Name", "?")
            fields = _role_field_order(role)
            has_not_actions = "NotActions" in role
            has_data_actions = "DataActions" in role
            has_not_data_actions = "NotDataActions" in role
            assignable_pos = fields.index("AssignableScopes") if "AssignableScopes" in fields else -1
            print(
                f"  {name}: fields={fields}, has_NotActions={has_not_actions}, "
                f"has_DataActions={has_data_actions}, has_NotDataActions={has_not_data_actions}, "
                f"AssignableScopes_position={assignable_pos}"
            )

    # --- Cross-reference our policies ---
    all_verified: list[str] = []
    all_unverified: list[str] = []
    all_missing_providers: set[str] = set()
    all_bad_pattern: list[str] = []

    for policy_file in OUR_POLICIES:
        if not policy_file.exists():
            print(f"MISSING: {policy_file}")
            sys.exit(1)

        try:
            policy = json.loads(policy_file.read_text())
        except json.JSONDecodeError as e:
            print(f"INVALID JSON in {policy_file}: {e}")
            sys.exit(1)

        our_actions = _actions_from_policy(policy)
        file_verified: list[str] = []
        file_unverified: list[str] = []
        file_missing: set[str] = set()
        file_bad_pattern: list[str] = []

        for action in our_actions:
            # Structural pattern check
            if not _check_action_structural_pattern(action):
                file_bad_pattern.append(action)
                file_unverified.append(action)
                continue

            provider = _extract_provider(action)
            if provider is None:
                file_verified.append(action)
                continue

            if provider in all_ref_providers:
                file_verified.append(action)
            else:
                file_unverified.append(action)
                file_missing.add(provider)

        all_verified.extend(file_verified)
        all_unverified.extend(file_unverified)
        all_missing_providers |= file_missing
        all_bad_pattern.extend(file_bad_pattern)

        print(f"\n--- {policy_file.name} ---")
        for action in our_actions:
            provider = _extract_provider(action)
            status = "VERIFIED" if action in file_verified else "UNVERIFIED"
            source = ", ".join(sorted(ref_sources.get(provider or "", {"none"})))
            if not _check_action_structural_pattern(action):
                status = "BAD_PATTERN"
                source = "none"
            print(f"  [{status}] {action:<70s}  source={source}")

        print(f"  Verified:   {len(file_verified)}")
        print(f"  Unverified: {len(file_unverified)}")
        if file_missing:
            print(f"  Providers not in any reference: {', '.join(sorted(file_missing))}")
        if file_bad_pattern:
            print(f"  Bad pattern: {len(file_bad_pattern)}")

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {len(all_verified)} verified, {len(all_unverified)} unverified")

    if all_unverified:
        print(f"\nUNVERIFIED ACTIONS ({len(all_unverified)}):")
        for a in all_unverified:
            provider = _extract_provider(a) or "???"
            status = "BAD_PATTERN" if a in all_bad_pattern else "UNVERIFIED"
            print(f"  [{status}] {a} (provider: {provider})")

    if all_missing_providers:
        print(f"\nMISSING PROVIDERS ({len(all_missing_providers)}):")
        for p in sorted(all_missing_providers):
            print(f"  {p} — no example of this provider in MS or GitHub references")
        print(
            "\nConsider adding a reference example for these providers to "
            "config/infra/azure-ms-reference-roles.json or azure-github-reference-roles.json"
        )

    if all_unverified:
        sys.exit(1)

    print("\nPASS: All actions verified against reference examples (MS + GitHub)")
    sys.exit(0)


if __name__ == "__main__":
    main()
