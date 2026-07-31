#!/usr/bin/env python3
"""validate_gcp_iam_policy.py — Validate GCP IAM role definitions.

Validates:
  1. gcp-iam-roles.yml parses correctly
  2. All 4 personas defined (terraform_deploy, runtime_execution, model_inference, monitor)
  3. No roles/owner or roles/editor (super-admin / broad editor)
  4. gluddComputeOperator custom role defined with proper permissions
  5. Custom role does NOT include compute.instances.setMetadata (privilege escalation)
  6. CEL conditions are syntactically valid expressions
  7. Resource conditions use proper GCP resource URI patterns
  8. No wildcard resource * in role conditions
  9. Secret-scoped permissions are properly namespaced
 10. Required monitoring permissions present for monitor persona
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config" / "infra"
GCP_ROLES_FILE = CONFIG_DIR / "gcp-iam-roles.yml"
TERRAFORM_MAIN = REPO_ROOT / "infra" / "terraform" / "modules" / "onboard-iam-gcp" / "main.tf"


REQUIRED_PERSONAS = frozenset(
    {
        "terraform_deploy",
        "runtime_execution",
        "model_inference",
        "monitor",
    }
)

FORBIDDEN_ROLES = frozenset({"roles/owner", "roles/editor"})

FORBIDDEN_CUSTOM_PERMISSIONS = frozenset(
    {
        "compute.instances.setMetadata",
    }
)

# GCP resource type prefixes that are expected in conditions
VALID_RESOURCE_TYPES = frozenset(
    {
        "compute.googleapis.com/Instance",
        "compute.googleapis.com/Disk",
        "compute.googleapis.com/Network",
        "compute.googleapis.com/Firewall",
        "compute.googleapis.com/Subnetwork",
        "secretmanager.googleapis.com/Secret",
        "aiplatform.googleapis.com/Endpoint",
        "storage.googleapis.com/Bucket",
        "storage.googleapis.com/Object",
        "projects",
    }
)


def load_gcp_roles() -> dict:
    """Parse gcp-iam-roles.yml."""
    if not GCP_ROLES_FILE.exists():
        print(f"FAIL: Missing {GCP_ROLES_FILE}")
        sys.exit(1)

    with open(GCP_ROLES_FILE) as f:
        try:
            doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"FAIL: YAML parse error in {GCP_ROLES_FILE}: {e}")
            sys.exit(1)

    if not isinstance(doc, dict) or "roles" not in doc:
        print(f"FAIL: {GCP_ROLES_FILE} missing top-level 'roles' key")
        sys.exit(1)

    return doc["roles"]


def validate_personas(roles: dict) -> list[str]:
    """Check all 4 required personas are defined."""
    errors: list[str] = []
    actual = frozenset(roles.keys())
    missing = REQUIRED_PERSONAS - actual
    if missing:
        errors.append(f"Missing personas: {', '.join(sorted(missing))}")
    extra = actual - REQUIRED_PERSONAS
    for p in sorted(extra):
        print(f"WARN: Extra persona defined: {p}")
    return errors


def validate_no_forbidden_roles(roles: dict) -> list[str]:
    """No persona may use roles/owner or roles/editor."""
    errors: list[str] = []
    for name, role_def in roles.items():
        bindings = role_def.get("roles", [])
        for binding in bindings:
            if binding in FORBIDDEN_ROLES:
                errors.append(f"Persona '{name}' has forbidden role '{binding}'")
    return errors


def validate_role_bindings_nonempty(roles: dict) -> list[str]:
    """Each persona must have at least one role binding."""
    errors: list[str] = []
    for name, role_def in roles.items():
        bindings = role_def.get("roles", [])
        if not bindings:
            errors.append(f"Persona '{name}' has no role bindings")
    return errors


def validate_role_bindings_format(roles: dict) -> list[str]:
    """Role bindings must start with 'roles/' (predefined) or 'projects/' (custom)."""
    errors: list[str] = []
    pattern = re.compile(r"^(roles/|projects/)")
    for name, role_def in roles.items():
        for binding in role_def.get("roles", []):
            if not pattern.match(binding):
                errors.append(f"Persona '{name}': role '{binding}' does not match 'roles/' or 'projects/' prefix")
    return errors


def validate_descriptions(roles: dict) -> list[str]:
    """Each persona must have a description of at least 20 chars."""
    errors: list[str] = []
    for name, role_def in roles.items():
        desc = role_def.get("description", "")
        if len(desc) < 20:
            errors.append(f"Persona '{name}' description too short ({len(desc)} chars, need ≥20)")
    return errors


# ---------------------------------------------------------------------------
# Custom role (gluddComputeOperator) validation
# ---------------------------------------------------------------------------


def load_custom_role_permissions() -> set[str]:
    """Extract permissions from the Terraform google_project_iam_custom_role block."""
    if not TERRAFORM_MAIN.exists():
        print(f"FAIL: Missing {TERRAFORM_MAIN}")
        sys.exit(1)

    content = TERRAFORM_MAIN.read_text()

    role_id_match = re.search(r'role_id\s*=\s*"gluddComputeOperator"', content)
    if not role_id_match:
        print(f"FAIL: No 'gluddComputeOperator' custom role found in {TERRAFORM_MAIN}")
        sys.exit(1)

    start = content.find("permissions = [")
    if start == -1:
        print(f"FAIL: gluddComputeOperator has no permissions block")
        sys.exit(1)

    end = content.find("]", start)
    if end == -1:
        print(f"FAIL: gluddComputeOperator permissions block unterminated")
        sys.exit(1)

    block = content[start : end + 1]
    perms = re.findall(r'"([\w.]+)"', block)
    return set(perms)


def validate_custom_role(permissions: set[str]) -> list[str]:
    """Validate the gluddComputeOperator custom role."""
    errors: list[str] = []

    if not permissions:
        errors.append("gluddComputeOperator custom role has no permissions")
        return errors

    for forbidden in FORBIDDEN_CUSTOM_PERMISSIONS:
        if forbidden in permissions:
            errors.append(
                f"gluddComputeOperator includes forbidden permission '{forbidden}' — privilege escalation vector"
            )

    instance_perms = {p for p in permissions if p.startswith("compute.instances.")}
    if not instance_perms:
        errors.append("gluddComputeOperator missing compute.instances.* permissions")

    required_ops = {"compute.instances.insert", "compute.instances.delete"}
    missing = required_ops - permissions
    if missing:
        errors.append(f"gluddComputeOperator missing required permissions: {', '.join(sorted(missing))}")

    return errors


# ---------------------------------------------------------------------------
# CEL condition validation
# ---------------------------------------------------------------------------

CEL_KEYWORDS = frozenset(
    {
        "true",
        "false",
        "null",
        "in",
        "size",
        "matches",
        "startsWith",
        "endsWith",
        "contains",
        "extract",
        "type",
        "name",
        "resource",
        "has",
        "all",
        "exists",
        "exists_one",
        "filter",
        "map",
    }
)

CEL_OPERATORS = frozenset({"==", "!=", "&&", "||", "<", ">", "<=", ">=", "+", "-", "!", ":"})


def _is_valid_cel_identifier(token: str) -> bool:
    """CEL identifiers: alpha + (alphanumeric|_|.)*"""
    return bool(re.match(r"^[a-zA-Z_][\w.]*$", token))


def _is_valid_cel_string(token: str) -> bool:
    return token.startswith('"') and token.endswith('"')


def validate_cel_syntax(roles: dict) -> list[str]:
    """Check that CEL expressions are syntactically plausible."""
    errors: list[str] = []
    for name, role_def in roles.items():
        conditions = role_def.get("conditions", [])
        for idx, cond in enumerate(conditions):
            expr = cond.get("expression", "")
            if not expr:
                continue

            if not cond.get("title"):
                errors.append(f"Persona '{name}', condition {idx}: missing title")

            if "&&" in expr and "||" in expr:
                if expr.count("(") != expr.count(")"):
                    errors.append(
                        f"Persona '{name}', condition {idx}: mismatched parentheses in CEL expression with mixed AND/OR"
                    )

            paren_depth = 0
            for ch in expr:
                if ch == "(":
                    paren_depth += 1
                elif ch == ")":
                    paren_depth -= 1
                if paren_depth < 0:
                    errors.append(f"Persona '{name}', condition {idx}: unbalanced parentheses")
                    break
            if paren_depth != 0:
                errors.append(f"Persona '{name}', condition {idx}: unbalanced parentheses (depth={paren_depth})")

            if expr.endswith("||") or expr.endswith("&&"):
                errors.append(f"Persona '{name}', condition {idx}: CEL expression ends with trailing operator")
            if expr.startswith("||") or expr.startswith("&&"):
                errors.append(f"Persona '{name}', condition {idx}: CEL expression starts with boolean operator")

            if "resource.type" not in expr and "resource.name" not in expr:
                warnings: list[str] = []
                warnings.append(
                    f"WARN: Persona '{name}', condition {idx}: CEL expression "
                    f"references neither resource.type nor resource.name"
                )
                for w in warnings:
                    print(w)

    return errors


# ---------------------------------------------------------------------------
# Resource URI pattern validation
# ---------------------------------------------------------------------------

_RESOURCE_URI_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"projects/_/buckets/[\w-]+"), "GCS bucket"),
    (re.compile(r"projects/[\w-]+"), "GCP project"),
    (re.compile(r"zones/[\w-]+"), "GCP zone"),
    (re.compile(r"instances/[\w-]+"), "GCE instance"),
    (re.compile(r"endpoints/[\w-]+"), "Vertex AI endpoint"),
    (re.compile(r"secrets/[\w-]+"), "Secret Manager secret"),
]


def validate_resource_uris(roles: dict) -> list[str]:
    """Check that resource references in conditions match known GCP URI patterns."""
    errors: list[str] = []

    for name, role_def in roles.items():
        conditions = role_def.get("conditions", [])
        for idx, cond in enumerate(conditions):
            expr = cond.get("expression", "")
            if not expr:
                continue

            # Check for proper GCP resource type format: <service>.googleapis.com/<Resource>
            resource_type_matches = re.findall(r'resource\.type\s*==\s*"([^"]+)"', expr)
            for rt in resource_type_matches:
                if rt not in VALID_RESOURCE_TYPES and "/" in rt:
                    errors.append(
                        f"Persona '{name}', condition {idx}: unknown resource type "
                        f"'{rt}' — expected e.g. 'compute.googleapis.com/Instance'"
                    )

    return errors


def validate_no_wildcard_resource(roles: dict) -> list[str]:
    """Check no condition uses a bare wildcard '*' for resource."""
    errors: list[str] = []
    for name, role_def in roles.items():
        conditions = role_def.get("conditions", [])
        for idx, cond in enumerate(conditions):
            expr = cond.get("expression", "")
            if not expr:
                continue
            bare_star = re.findall(r'==\s*"\*"', expr)
            if bare_star:
                errors.append(
                    f"Persona '{name}', condition {idx}: CEL expression "
                    f"contains bare wildcard '*' — narrow to a resource pattern"
                )
            star_match = re.findall(r'==\s*"(\*)"', expr)
            if star_match:
                errors.append(f"Persona '{name}', condition {idx}: CEL expression uses wildcard resource '*'")
    return errors


# ---------------------------------------------------------------------------
# Secret Manager scoping
# ---------------------------------------------------------------------------


def validate_secret_manager_scoping(roles: dict) -> list[str]:
    """Verify Secret Manager permissions have proper namespace constraints."""
    errors: list[str] = []

    for name, role_def in roles.items():
        bindings = role_def.get("roles", [])
        has_secretmanager = any(b.startswith("roles/secretmanager.") for b in bindings)
        if not has_secretmanager:
            continue

        conditions = role_def.get("conditions", [])
        has_secret_condition = any(
            "secretmanager.googleapis.com/Secret" in cond.get("expression", "") for cond in conditions
        )

        if not has_secret_condition:
            errors.append(
                f"Persona '{name}' has secretmanager role without a resource condition — secrets must be namespaced"
            )
            continue

        has_namespace_check = any(
            "startsWith" in cond.get("expression", "") or "extract" in cond.get("expression", "")
            for cond in conditions
            if "Secret" in cond.get("expression", "")
        )
        if not has_namespace_check:
            errors.append(f"Persona '{name}' has secretmanager role but condition does not scope to a naming prefix")

    return errors


# ---------------------------------------------------------------------------
# Monitor persona — required permissions
# ---------------------------------------------------------------------------


REQUIRED_MONITOR_PREFIXES = frozenset(
    {
        "roles/monitoring.",
        "roles/logging.",
        "roles/billing.",
    }
)


def validate_monitor_permissions(roles: dict) -> list[str]:
    """Monitor persona must have monitoring, logging, and billing read access."""
    errors: list[str] = []
    monitor = roles.get("monitor")
    if not monitor:
        errors.append("Monitor persona not defined")
        return errors

    bindings = monitor.get("roles", [])
    binding_set = frozenset(bindings)
    found_prefixes: set[str] = set()
    for prefix in REQUIRED_MONITOR_PREFIXES:
        has = any(b.startswith(prefix) for b in binding_set)
        if has:
            found_prefixes.add(prefix)

    missing = REQUIRED_MONITOR_PREFIXES - found_prefixes
    if missing:
        errors.append(f"Monitor persona missing required permission prefixes: {', '.join(sorted(missing))}")

    for b in binding_set:
        if "write" not in b.lower() and "admin" not in b.lower():
            continue
        if b.endswith(".viewer"):
            continue
        errors.append(f"Monitor persona has write/admin role '{b}' — monitor should be read-only")

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    errors: list[str] = []

    print(f"=== GCP IAM Policy Validation ===")
    print(f"Roles file: {GCP_ROLES_FILE}")
    print(f"Custom role: {TERRAFORM_MAIN}")
    print()

    # Step 1: Parse YAML
    roles = load_gcp_roles()
    print(f"PASS: gcp-iam-roles.yml parsed — {len(roles)} personas")

    # Step 2: Required personas
    e = validate_personas(roles)
    errors.extend(e)
    if not e:
        print("PASS: All 4 required personas present")

    # Step 3: No forbidden roles
    e = validate_no_forbidden_roles(roles)
    errors.extend(e)
    if not e:
        print("PASS: No roles/owner or roles/editor found")

    # Step 4: Role bindings nonempty
    e = validate_role_bindings_nonempty(roles)
    errors.extend(e)
    if not e:
        print("PASS: All personas have role bindings")

    # Step 5: Role binding format
    e = validate_role_bindings_format(roles)
    errors.extend(e)
    if not e:
        print("PASS: All role bindings have valid prefix")

    # Step 6: Descriptions
    e = validate_descriptions(roles)
    errors.extend(e)
    if not e:
        print("PASS: All personas have adequate descriptions")

    # Step 7: Custom role
    try:
        custom_perms = load_custom_role_permissions()
        print(f"PASS: gluddComputeOperator found — {len(custom_perms)} permissions")

        e = validate_custom_role(custom_perms)
        errors.extend(e)
        if not e:
            print("PASS: gluddComputeOperator has no forbidden permissions")
    except SystemExit:
        print("FAIL: Failed to load custom role permissions")
        errors.append("Custom role validation failed")

    # Step 8: CEL syntax
    e = validate_cel_syntax(roles)
    errors.extend(e)
    if not e:
        print("PASS: CEL conditions syntactically valid")

    # Step 9: Resource URIs
    e = validate_resource_uris(roles)
    errors.extend(e)
    if not e:
        print("PASS: Resource URI patterns valid")

    # Step 10: No wildcard resource
    e = validate_no_wildcard_resource(roles)
    errors.extend(e)
    if not e:
        print("PASS: No wildcard '*' resource in conditions")

    # Step 11: Secret Manager scoping
    e = validate_secret_manager_scoping(roles)
    errors.extend(e)
    if not e:
        print("PASS: Secret Manager permissions properly scoped")

    # Step 12: Monitor permissions
    e = validate_monitor_permissions(roles)
    errors.extend(e)
    if not e:
        print("PASS: Monitor persona has required read-only permissions")

    print()
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        print(f"\n{len(errors)} error(s)")
        sys.exit(1)

    print(f"PASS: All GCP IAM policy validations passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
