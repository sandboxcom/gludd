"""Cloud IAM core — main entry points for generating and validating
least-privilege roles across Azure, AWS, and GCP.
"""

from __future__ import annotations

from typing import Any

from general_ludd.cloud.aws_validator import validate_aws_role
from general_ludd.cloud.azure_validator import (
    validate_against_azure_schema,
)
from general_ludd.cloud.gcp_validator import validate_gcp_role
from general_ludd.cloud.role_generator import generate_role_from_template

SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"azure", "aws", "gcp"})

CROSS_PROVIDER_PATTERNS: dict[str, str] = {
    "wildcard_resource_all": "Using wildcard '*' as the sole resource — must narrow to specific ARNs / scopes",
    "owner_role_assignment": "Granting owner / admin equivalent — prefer least-privilege custom roles",
    "missing_deny_block": "Runtime-execution persona missing explicit Deny block for privilege-escalation paths",
    "write_access_on_root": "Write access assigned at root scope '/' — must scope to subscription / project / account",
    "secret_key_read_not_action": "Key/secret reading uses /read instead of /action — provider convention violation",
    "runcommand_allowed": "virtualMachines/runCommand/action not in NotActions — must deny remote code execution",
    "setmetadata_allowed": "compute.instances.setMetadata allowed — metadata-based SSH key escalation risk",
    "passrole_unscoped": "iam:PassRole Allow without Condition — must scope to specific role ARNs",
    "fulladmin_managed_policy": "AdministratorAccess / PowerUserAccess / roles/owner inline — use custom roles",
    "no_condition_on_sensitive": "Sensitive permission allowed without attribute-based condition (CEL / Condition)",
}


def generate_cloud_role(
    provider: str,
    persona: str,
    resource_types: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a least-privilege cloud IAM role for *provider* and *persona*.

    Delegates to ``role_generator.generate_role_from_template``, then runs
    provider-specific validation on the result.
    """
    if provider not in SUPPORTED_PROVIDERS:
        return {
            "status": "error",
            "role_definition": {},
            "warnings": [f"Unsupported provider {provider!r}. Supported: {sorted(SUPPORTED_PROVIDERS)}"],
        }

    generated = generate_role_from_template(provider, persona, resource_types)
    if generated["status"] == "error":
        return generated

    role_def = generated["role_definition"]
    validation = validate_cloud_role(provider, role_def)

    final_status = "ok"
    final_warnings = list(generated["warnings"])
    if validation["status"] == "invalid":
        final_status = "generated_with_warnings"
        final_warnings.append(f"Validation flagged {len(validation['errors'])} issue(s): {validation['errors']}")

    final_warnings.extend(validation.get("warnings", []))

    return {
        "status": final_status,
        "role_definition": role_def,
        "warnings": final_warnings,
    }


def validate_cloud_role(provider: str, role_definition: dict[str, Any]) -> dict[str, Any]:
    """Validate a cloud IAM role definition for *provider*.

    Delegates to the provider-specific validator and cross-checks against
    ``CROSS_PROVIDER_PATTERNS``.
    """
    if provider not in SUPPORTED_PROVIDERS:
        return {
            "status": "error",
            "errors": [f"Unsupported provider {provider!r}. Supported: {sorted(SUPPORTED_PROVIDERS)}"],
            "warnings": [],
        }

    result: dict[str, Any] = {"status": "valid", "errors": [], "warnings": []}

    if provider == "azure":
        ok, messages = validate_against_azure_schema(role_definition)
        if not ok:
            errors = [m for m in messages if not m.startswith("WARNING:")]
            warnings = [m for m in messages if m.startswith("WARNING:")]
            result["errors"] = errors
            result["warnings"] = warnings
            if errors:
                result["status"] = "invalid"

    elif provider == "aws":
        aws_result = validate_aws_role(role_definition)
        result["errors"] = aws_result.get("errors", [])
        result["warnings"] = aws_result.get("warnings", [])
        if aws_result.get("status") == "invalid":
            result["status"] = "invalid"

    elif provider == "gcp":
        bindings = role_definition.get("bindings", [])
        persona = role_definition.get("role_name", "")
        gcp_result = validate_gcp_role(persona, bindings)
        result["errors"] = gcp_result.get("errors", [])
        result["warnings"] = gcp_result.get("warnings", [])
        if gcp_result.get("status") == "invalid":
            result["status"] = "invalid"

    cross_issues = _check_cross_provider(provider, role_definition)
    result["warnings"].extend(cross_issues)

    return result


def _check_cross_provider(provider: str, role_def: dict[str, Any]) -> list[str]:
    """Apply cross-provider anti-pattern checks."""
    warnings: list[str] = []

    if provider == "azure":
        assignable_scopes = role_def.get("AssignableScopes", [])
        if "/" in assignable_scopes:
            warnings.append(f"{CROSS_PROVIDER_PATTERNS['write_access_on_root']}")
        actions = role_def.get("Actions", [])
        has_runcommand = any("runCommand/action" in a for a in actions)
        if has_runcommand:
            not_actions = role_def.get("NotActions", [])
            if not any("runCommand" in na for na in not_actions):
                warnings.append(f"{CROSS_PROVIDER_PATTERNS['runcommand_allowed']}")

    elif provider == "aws":
        for stmt in role_def.get("policy", []):
            if not isinstance(stmt, dict):
                continue
            actions = stmt.get("Action", [])
            if isinstance(actions, list):
                if "iam:PassRole" in actions and stmt.get("Effect") == "Allow" and "Condition" not in stmt:
                    warnings.append(f"{CROSS_PROVIDER_PATTERNS['passrole_unscoped']}")

    elif provider == "gcp":
        bindings = role_def.get("bindings", [])
        for b in bindings:
            if isinstance(b, dict) and b.get("role") == "roles/owner":
                warnings.append(f"{CROSS_PROVIDER_PATTERNS['owner_role_assignment']}")

    return warnings


__all__ = [
    "CROSS_PROVIDER_PATTERNS",
    "SUPPORTED_PROVIDERS",
    "generate_cloud_role",
    "validate_cloud_role",
]
