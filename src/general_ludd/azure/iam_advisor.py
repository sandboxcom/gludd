"""Azure IAM advisor — persona-to-role mapping and assignment auditing."""

from __future__ import annotations

from typing import Any

PERSONA_ROLE_MAP: dict[str, list[str]] = {
    "developer": ["Contributor"],
    "operator": ["Reader", "Contributor"],
    "auditor": ["Reader"],
    "admin": ["Owner"],
}

_OVER_PRIVILEGED_ROLES: frozenset[str] = frozenset({"Owner", "Contributor"})
_RISKY_SCOPES: frozenset[str] = frozenset({"/", "/subscriptions"})


def recommend_roles_for_persona(persona: str) -> list[str]:
    """Return the recommended Azure role(s) for a persona.

    Returns an empty list when the persona is unknown.
    """
    normalized = persona.strip().lower()
    return PERSONA_ROLE_MAP.get(normalized, [])


def audit_existing_assignments(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Audit existing IAM assignments for over-privileged configurations.

    Flags any assignment where a highly privileged role (Owner, Contributor)
    is granted at a subscription-or-higher scope.
    """
    findings: list[dict[str, Any]] = []
    for assignment in assignments:
        role = assignment.get("role", "")
        scope = assignment.get("scope", "")
        if role in _OVER_PRIVILEGED_ROLES and _is_subscription_scope(scope):
            findings.append(
                {
                    **assignment,
                    "over_privileged": True,
                    "reason": f"Role '{role}' at scope '{scope}' is over-privileged",
                }
            )
    return findings


def _is_subscription_scope(scope: str) -> bool:
    return bool(scope) and scope in _RISKY_SCOPES
