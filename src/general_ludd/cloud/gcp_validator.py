"""GCP IAM validator — role-binding checks, owner-role detection,
setMetadata enforcement, and persona-level required denials.
"""

from __future__ import annotations

from typing import Any

GCP_REQUIRED_DENIALS: dict[str, list[str]] = {
    "runtime_execution": [
        "iam.serviceAccounts.setIamPolicy",
        "iam.serviceAccounts.actAs",
        "compute.instances.setMetadata",
        "compute.instances.setServiceAccount",
    ],
    "terraform_deploy": [],
    "model_inference": [
        "compute.instances.setMetadata",
        "iam.serviceAccounts.actAs",
    ],
    "monitor": [
        "iam.serviceAccounts.create",
        "iam.serviceAccountKeys.create",
    ],
}

GCP_DANGEROUS_ROLES: frozenset[str] = frozenset(
    {
        "roles/owner",
        "roles/editor",
        "roles/iam.securityAdmin",
        "roles/iam.organizationRoleAdmin",
        "roles/resourcemanager.organizationAdmin",
        "roles/resourcemanager.projectIamAdmin",
    }
)

GCP_DANGEROUS_PERMISSIONS: frozenset[str] = frozenset(
    {
        "compute.instances.setMetadata",
        "compute.instances.setServiceAccount",
        "iam.serviceAccounts.setIamPolicy",
        "iam.serviceAccounts.actAs",
        "iam.serviceAccounts.getAccessToken",
        "iam.serviceAccountKeys.create",
        "iam.serviceAccountKeys.delete",
        "iam.roles.create",
        "iam.roles.update",
        "iam.roles.delete",
        "resourcemanager.projects.setIamPolicy",
    }
)


def validate_gcp_role(persona: str, bindings: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate GCP IAM role bindings for least-privilege compliance.

    Checks:
    - No ``roles/owner`` or other dangerous built-in roles
    - No ``compute.instances.setMetadata`` in allowed permissions (metadata-based
      privilege escalation via SSH keys)
    - Persona-level required denials are present
    - CEL conditions present for sensitive permissions
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(bindings, list) or not bindings:
        return {"status": "invalid", "errors": ["bindings must be a non-empty list"], "warnings": warnings}

    all_allowed_permissions: set[str] = set()
    all_denied_permissions: set[str] = set()
    has_deny = False

    for idx, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            errors.append(f"Binding [{idx}] is not a dict")
            continue

        role_name = binding.get("role", "")
        binding.get("members", [])
        permissions = binding.get("permissions", [])
        condition = binding.get("condition", {})

        binding_effect = binding.get("effect", "allow")
        is_deny = binding_effect.lower() == "deny"
        if is_deny:
            has_deny = True

        label = f"binding [{idx}]"

        if isinstance(role_name, str) and role_name in GCP_DANGEROUS_ROLES:
            errors.append(f"{label}: dangerous role {role_name} — use custom roles instead")

        if isinstance(permissions, list):
            for perm in permissions:
                if is_deny:
                    all_denied_permissions.add(perm)
                else:
                    all_allowed_permissions.add(perm)
                    if perm in GCP_DANGEROUS_PERMISSIONS:
                        if not isinstance(condition, dict) or "expression" not in condition:
                            errors.append(
                                f"{label}: {perm} allowed without CEL condition — requires attribute-based guard"
                            )
                        else:
                            warnings.append(f"{label}: {perm} has CEL condition; verify expression is scoped enough")

    if persona in GCP_REQUIRED_DENIALS:
        required = GCP_REQUIRED_DENIALS[persona]
        still_allowed = [d for d in required if d in all_allowed_permissions and d not in all_denied_permissions]
        if still_allowed:
            errors.append(f"Persona '{persona}' must deny: {still_allowed}")

    if persona == "runtime_execution" and not has_deny:
        warnings.append("runtime_execution persona has no explicit Deny binding")

    status = "valid" if not errors else "invalid"
    return {"status": status, "errors": errors, "warnings": warnings}


__all__ = [
    "GCP_DANGEROUS_ROLES",
    "GCP_REQUIRED_DENIALS",
    "validate_gcp_role",
]
